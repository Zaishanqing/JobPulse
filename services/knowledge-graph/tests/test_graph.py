from sqlalchemy import select
from app.models import (StandardPosition, Skill, SkillCategory, PositionCategory, GraphBuildRun,
    GraphBuildSample, PositionSkillRelationDraft, GraphVersion, ReviewTask, AuditLog)
from tests.factories import approve_build_tasks

def prepare_catalog(db):
    db.add_all([PositionCategory(code="TECH",name="技术"),SkillCategory(code="LANG",name="语言"),
        StandardPosition(
            position_id="POS_BACKEND",
            position_code="POS_BACKEND",
            name="后端工程师",
            category_code="TECH",
            taxonomy_version="position-taxonomy.v3.0.0",
            sample_support_status="sufficient",
        ),
        Skill(skill_id="SKILL_PYTHON",canonical_name="Python",category_code="LANG")]); db.commit()

def prepare_jd(client,headers,doc_id="JD1",title="后端工程师",skill="Python",quote="熟悉 Python",modality="required"):
    raw=f"{title}\n{quote}\n负责服务开发\n本科\n三年经验\n沟通能力\n上海\n科技企业"
    client.post("/api/v1/jds",json={"document_id":doc_id,"raw_text":raw,"source_type":"synthetic_demo","is_synthetic":True},headers=headers)
    payload={"document_id":doc_id,"job_title":{"text":title,"evidence":{"source_id":doc_id,"quote":title}},
      "responsibilities":[{"requirement_id":"t1","text":"负责服务开发","evidence":{"source_id":doc_id,"quote":"负责服务开发"}}],
      "requirements":[
       {"requirement_id":"r1","kind":"skill","modality":modality,"evidence":{"source_id":doc_id,"quote":quote},"items":[{"name":skill}]},
       {"requirement_id":"r2","kind":"education","modality":"required","evidence":{"source_id":doc_id,"quote":"本科"},"text":"本科"},
       {"requirement_id":"r3","kind":"experience","modality":"required","evidence":{"source_id":doc_id,"quote":"三年经验"},"text":"三年经验"},
       {"requirement_id":"r4","kind":"soft_skill","modality":"unknown","evidence":{"source_id":doc_id,"quote":"沟通能力"},"text":"沟通能力"}],
      "company_facts":[{"fact_id":"c1","text":"科技企业","evidence":{"source_id":doc_id,"quote":"科技企业"}}],
      "employment_facts":[{"fact_id":"e1","fact_type":"location","text":"上海","evidence":{"source_id":doc_id,"quote":"上海"}}]}
    client.post(f"/api/v1/jds/{doc_id}/extraction-result/import",json=payload,headers=headers)
    client.post(f"/api/v1/jds/{doc_id}/extraction-result/align",headers=headers)
    resolved = title == "后端工程师"
    normalized_skill = skill == "Python"
    normalized = {
        "schema_version": "v2",
        "document_id": doc_id,
        "job_classification": {
            "schema_version": "job-position-classification.v3",
            "taxonomy_version": "position-taxonomy.v3.0.0",
            "source_title": title,
            "position_code": "POS_BACKEND" if resolved else None,
            "position_name": "后端工程师" if resolved else None,
            "family_code": "SOFTWARE_ENGINEERING" if resolved else None,
            "family_name": "软件工程与研发" if resolved else None,
            "candidate_positions": (
                [{"position_code": "POS_BACKEND", "score": 0.93}]
                if resolved else []
            ),
            "career_level": "mid" if resolved else None,
            "leadership_scope": "none" if resolved else None,
            "technology_focus_codes": [],
            "industry_context_codes": [],
            "observed_skill_domain_codes": (
                ["software_engineering"] if resolved else []
            ),
            "confidence": 0.93 if resolved else 0.0,
            "classification_status": "resolved" if resolved else "catalog_gap",
            "review_reason_codes": [] if resolved else ["NO_SUITABLE_POSITION"],
            "evidence_refs": ["t1", "r1"] if resolved else ["r1"],
            "classification_policy_version": "position-classifier.v3.0",
        },
        "normalized_requirements": [{
            "requirement_id": "r1",
            "kind": "skill",
            "normalized_skills": [{
                "source_name": skill,
                "skill_id": "SKILL_PYTHON" if normalized_skill else None,
                "canonical_name": "Python" if normalized_skill else None,
                "category_code": "LANG" if normalized_skill else None,
                "subcategory_code": None,
                "resolution_status": "resolved" if normalized_skill else "unresolved",
                "resolution_source": "explicit_mapping" if normalized_skill else "unresolved",
            }],
        }],
        "salary": None,
        "unresolved_items": (
            []
            if resolved and normalized_skill
            else [
                *([] if resolved else [{
                    "source_name": title,
                    "item_type": "position",
                    "reason": "no suitable position in position-taxonomy.v3",
                }]),
                *([] if normalized_skill else [{
                    "source_name": skill,
                    "item_type": "skill",
                    "reason": "no exact normalized mapping",
                }]),
            ]
        ),
    }
    response = client.post(
        f"/api/v1/jds/{doc_id}/normalized-result/import",
        json=normalized,
        headers=headers,
    )
    assert response.status_code == 200, response.text
    client.post(f"/api/v1/jds/{doc_id}/duplicate-check",json={},headers=headers)

def test_build_only_resolved_skills_and_publish_rollback(client,db,auth_headers):
    headers=auth_headers(); prepare_catalog(db); prepare_jd(client,headers)
    build=client.post("/api/v1/positions/POS_BACKEND/graph/build",json={},headers=headers).json()["data"]
    assert build["status"]=="succeeded" and build["summary"]["relations"]==1
    rel=db.scalar(select(PositionSkillRelationDraft)); assert rel.auto_weight==rel.final_weight and rel.auto_confidence==rel.final_confidence
    assert rel.metrics["support_document_count"]==1 and rel.metrics["trusted_evidence_ratio"]==1
    approve_build_tasks(client,build["build_run_id"],headers)
    published=client.post(f'/api/v1/graph/build-runs/{build["build_run_id"]}/publish',json={"reason":"approved","actor_id":999999},headers=headers).json()["data"]
    v1=db.get(GraphVersion,published["version_id"]); assert v1.snapshot["skill_relations"][0]["skill_id"]=="SKILL_PYTHON"
    graph=client.get("/api/v1/positions/POS_BACKEND/graph").json()["data"]; assert graph["position_id"]=="POS_BACKEND"
    assert graph["build_run_id"]==build["build_run_id"]
    assert graph["build_info"]["build_version"]==1
    assert graph["build_info"]["status"]=="published"
    assert {x["kind"] for x in graph["requirement_profile"]}=={"education","experience","soft_skill"}
    assert graph["company_context"][0]["kind"]=="company_fact"
    assert graph["employment_context"][0]["kind"]=="employment_fact"
    assert graph["task_profile"][0]["text"]=="负责服务开发"
    requirement_id=graph["requirement_profile"][0]["aggregate_id"]
    task_id=graph["task_profile"][0]["aggregate_id"]
    company_id=graph["company_context"][0]["aggregate_id"]
    employment_id=graph["employment_context"][0]["aggregate_id"]
    assert client.get(f"/api/v1/requirements/{requirement_id}/evidence",headers=headers).status_code==200
    assert client.get(f"/api/v1/tasks/{task_id}/evidence",headers=headers).json()["data"][0]["evidence"]["alignment"]=="exact"
    company_evidence=client.get(f"/api/v1/company_facts/{company_id}/evidence",headers=headers).json()["data"]
    employment_evidence=client.get(f"/api/v1/employment_facts/{employment_id}/evidence",headers=headers).json()["data"]
    assert company_evidence[0]["evidence"]["alignment"]=="exact"
    assert employment_evidence[0]["evidence"]["alignment"]=="exact"
    assert "科技企业" in company_evidence[0]["source"]["raw_text"]
    assert "上海" in employment_evidence[0]["source"]["raw_text"]
    assert len(client.get("/api/v1/positions/POS_BACKEND/graph/visualization").json()["data"]["edges"])==1
    build2=client.post("/api/v1/positions/POS_BACKEND/graph/build",json={},headers=headers).json()["data"]
    approve_build_tasks(client,build2["build_run_id"],headers)
    p2=client.post(f'/api/v1/graph/build-runs/{build2["build_run_id"]}/publish',json={},headers=headers).json()["data"]
    graph2=client.get("/api/v1/positions/POS_BACKEND/graph").json()["data"]
    assert graph2["build_info"]["build_version"]==2
    assert graph2["build_info"]["base_build_version"]==1
    diff=client.get(f'/api/v1/positions/POS_BACKEND/graph/versions/diff?from_version_id={v1.id}&to_version_id={p2["version_id"]}').json()["data"]; assert diff["added"]==[]
    client.post("/api/v1/positions/POS_BACKEND/graph/build",json={},headers=headers)
    rolled=client.post(f"/api/v1/positions/POS_BACKEND/graph/versions/{v1.id}/rollback",json={"reason":"restore"},headers=headers).json()["data"]
    assert rolled["rollback_from_version_id"]==v1.id and rolled["version_number"]==3
    positions=client.get("/api/v1/positions").json()["data"]
    assert positions[0]["current_version_number"]==4
def test_unresolved_and_non_skill_never_aggregate(client,db,auth_headers):
    headers=auth_headers(); prepare_catalog(db); prepare_jd(client,headers,"BAD","未知岗位","MysterySkill","熟悉 MysterySkill")
    build=client.post("/api/v1/positions/POS_BACKEND/graph/build",json={},headers=headers).json()["data"]
    assert build["summary"]["relations"]==0
    sample=db.scalar(select(GraphBuildSample)); assert not sample.included and "position_mismatch_or_unresolved" in sample.exclusion_reasons
    items=client.get("/api/v1/normalization/unresolved-items",headers=auth_headers("reviewer")).json()["data"]; assert {x["item_type"] for x in items}=={"position","skill"}
    result=client.get("/api/v1/jds/BAD/normalized-result",headers=headers).json()["data"]; assert all(r["kind"]!="skill" or all(s["resolution_status"]=="unresolved" for s in r["normalized_skills"]) for r in result["normalized_requirements"])
def test_unknown_only_skill_modality_is_not_made_publishable(client,db,auth_headers):
    headers=auth_headers(); prepare_catalog(db); prepare_jd(client,headers,modality="unknown")
    build=client.post("/api/v1/positions/POS_BACKEND/graph/build",json={},headers=headers).json()["data"]
    assert build["summary"]["included_samples"]==1
    assert build["summary"]["relations"]==0
def test_relation_manual_values_preserve_auto_and_audit(client,db,auth_headers):
    headers=auth_headers(); prepare_catalog(db); prepare_jd(client,headers); client.post("/api/v1/positions/POS_BACKEND/graph/build",json={},headers=headers)
    rel=db.scalar(select(PositionSkillRelationDraft)); auto=rel.auto_weight
    assert client.post(f"/api/v1/relations/{rel.id}/modify",json={"build_run_id":rel.build_run_id,"position_id":rel.position_id,"expected_revision":rel.revision,"weight":.9,"confidence":.95,"importance_level":"core","reason":"expert review"},headers=headers).status_code==200
    db.refresh(rel); assert rel.auto_weight==auto and rel.manual_weight==rel.final_weight==.9 and db.scalar(select(AuditLog)).reason=="expert review"
    evidence=client.get(f"/api/v1/relations/{rel.id}/evidence",headers=headers).json()["data"]; assert evidence[0]["evidence"]["alignment"]=="exact"
def test_review_workflow(client,db,auth_headers,users):
    db.add(ReviewTask(object_type="evidence",object_id="1",payload={})); db.commit(); headers=auth_headers("reviewer")
    assert client.post("/api/v1/review-tasks/1/claim",json={"reason":"take task","actor_id":999},headers=headers).json()["data"]["status"]=="claimed"
    assert client.post("/api/v1/review-tasks/1/modify",json={"reason":"record check","actor_id":999,"payload":{"note":"checked"}},headers=headers).json()["data"]["status"]=="modified"
    assert client.post("/api/v1/review-tasks/1/approve",json={"actor_id":999,"reason":"exact"},headers=headers).json()["data"]["status"]=="approved"
    assert len(client.get("/api/v1/review-tasks",headers=headers).json()["data"])==1
