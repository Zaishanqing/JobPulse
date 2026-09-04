from app.models import PositionCategory, Skill, SkillCategory, StandardPosition

def prepare_catalog(db):
    db.add_all([
        PositionCategory(code="TECH",name="技术"), SkillCategory(code="LANG",name="语言"),
        StandardPosition(
            position_id="BACKEND_ENGINEER",
            position_code="BACKEND_ENGINEER",
            name="后端工程师",
            category_code="TECH",
            taxonomy_version="position-taxonomy.v3.0.0",
            sample_support_status="sufficient",
            status="active",
        ),
        Skill(skill_id="SKILL_PYTHON",canonical_name="Python",category_code="LANG"),
    ]); db.commit()

def prepare_jd(
    client,
    headers,
    doc_id="JD1",
    modality="required",
    skill="Python",
    raw_text=None,
):
    known_skill = skill == "Python"
    quote=f"熟悉 {skill}"; raw=f"后端工程师\n{quote}\n负责服务开发\n本科及以上"
    if raw_text is not None:
        raw = raw_text
    assert client.post("/api/v1/jds",json={"document_id":doc_id,"raw_text":raw,"source_type":"test","source_name":"board-a","enterprise_name":"enterprise-a"},headers=headers).status_code==200
    payload={"document_id":doc_id,"job_title":{"text":"后端工程师","evidence":{"source_id":doc_id,"quote":"后端工程师"}},"responsibilities":[{"requirement_id":"t1","text":"负责服务开发","evidence":{"source_id":doc_id,"quote":"负责服务开发"}}],"requirements":[{"requirement_id":"r1","kind":"skill","modality":modality,"evidence":{"source_id":doc_id,"quote":quote},"items":[{"name":skill}]},{"requirement_id":"r2","kind":"education","modality":"required","evidence":{"source_id":doc_id,"quote":"本科及以上"},"text":"本科及以上"}],"company_facts":[],"employment_facts":[]}
    assert client.post(f"/api/v1/jds/{doc_id}/extraction-result/import",json=payload,headers=headers).status_code==200
    assert client.post(f"/api/v1/jds/{doc_id}/extraction-result/align",headers=headers).status_code==200
    normalized = {
        "schema_version": "v2",
        "document_id": doc_id,
        "job_classification": {
            "schema_version": "job-position-classification.v3",
            "taxonomy_version": "position-taxonomy.v3.0.0",
            "source_title": "后端工程师",
            "position_code": "BACKEND_ENGINEER",
            "position_name": "后端工程师",
            "family_code": "SOFTWARE_ENGINEERING",
            "family_name": "软件工程与研发",
            "candidate_positions": [
                {"position_code": "BACKEND_ENGINEER", "score": 0.93}
            ],
            "career_level": "mid",
            "leadership_scope": "none",
            "technology_focus_codes": [],
            "industry_context_codes": [],
            "observed_skill_domain_codes": ["software_engineering"],
            "confidence": 0.93,
            "classification_status": "resolved",
            "review_reason_codes": [],
            "evidence_refs": ["t1", "r1"],
            "classification_policy_version": "position-classifier.v3.0",
        },
        "normalized_requirements": [
            {
                "requirement_id": "r1",
                "kind": "skill",
                "normalized_skills": [
                    {
                        "source_name": skill,
                        "skill_id": "SKILL_PYTHON" if known_skill else None,
                        "canonical_name": skill if known_skill else None,
                        "category_code": "LANG" if known_skill else None,
                        "subcategory_code": None,
                        "resolution_status": "resolved" if known_skill else "unresolved",
                        "resolution_source": "explicit_mapping" if known_skill else "unresolved",
                    }
                ],
            },
            {"requirement_id": "r2", "kind": "education", "normalized_skills": []},
        ],
        "salary": None,
        "unresolved_items": [] if known_skill else [{
            "source_name": skill,
            "item_type": "skill",
            "reason": "no exact normalized mapping",
        }],
    }
    assert client.post(
        f"/api/v1/jds/{doc_id}/normalized-result/import",
        json=normalized,
        headers=headers,
    ).status_code==200
    assert client.post(f"/api/v1/jds/{doc_id}/duplicate-check",json={},headers=headers).status_code==200

def approve_build_tasks(client, build_run_id, headers):
    tasks = client.get("/api/v1/review-tasks", headers=headers).json()["data"]
    for task in tasks:
        if task["build_run_id"] != build_run_id or task["status"] != "pending":
            continue
        assert client.post(
            f'/api/v1/review-tasks/{task["id"]}/claim',
            json={"reason": "review build output"}, headers=headers
        ).status_code == 200
        assert client.post(
            f'/api/v1/review-tasks/{task["id"]}/approve',
            json={"reason": "evidence verified"}, headers=headers
        ).status_code == 200


def valid_build(client, db, headers, **kwargs):
    prepare_catalog(db); prepare_jd(client,headers,**kwargs)
    response=client.post("/api/v1/positions/BACKEND_ENGINEER/graph/build",json={},headers=headers)
    assert response.status_code==200
    build = response.json()["data"]
    approve_build_tasks(client, build["build_run_id"], headers)
    return build
