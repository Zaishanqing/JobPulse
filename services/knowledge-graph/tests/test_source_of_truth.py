from copy import deepcopy

from sqlalchemy import select

from app.models import (
    ExtractedCandidateRequirement, ExtractedJobTitle, ExtractionEvidence,
    JDExtractionRecord, PositionSkillRelationDraft, Skill, SkillCategory,
)
from app.schemas.extraction import JDExtractionResult
from tests.factories import prepare_catalog, prepare_jd


def test_alignment_does_not_mutate_import_audit_payload(client, db, auth_headers):
    headers = auth_headers()
    prepare_catalog(db)
    prepare_jd(client, headers, doc_id="AUDIT1")
    record = db.scalar(select(JDExtractionRecord).where(JDExtractionRecord.document_id == "AUDIT1"))
    original = deepcopy(record.payload)
    client.post("/api/v1/jds/AUDIT1/extraction-result/align", headers=headers)
    db.refresh(record)
    assert record.payload == original

    evidence = db.scalar(select(ExtractionEvidence).where(
        ExtractionEvidence.document_id == "AUDIT1",
        ExtractionEvidence.owner_type == "requirement",
        ExtractionEvidence.owner_ref == "r1",
    ))
    document_text = "后端工程师\n熟悉 Python\n负责服务开发\n本科及以上"
    evidence.quote = "Python"
    evidence.start = document_text.index("Python")
    evidence.end = evidence.start + len("Python")
    evidence.alignment = "exact"
    db.commit()
    returned = client.get(
        "/api/v1/jds/AUDIT1/extraction-result", headers=headers
    ).json()["data"]
    assert returned["requirements"][0]["evidence"]["quote"] == "Python"
    db.refresh(record)
    assert record.payload == original


def test_normalization_response_is_rebuilt_from_structured_facts(
    client, db, auth_headers
):
    headers = auth_headers()
    prepare_catalog(db)
    prepare_jd(client, headers, doc_id="FACT1", skill="Mystery")
    client.post("/api/v1/jds/FACT1/extraction-result/align", headers=headers)
    client.post("/api/v1/jds/FACT1/normalize", headers=headers)
    item = client.get("/api/v1/normalization/unresolved-items", headers=headers).json()["data"][0]
    client.post(f"/api/v1/normalization/unresolved-items/{item['id']}/resolve",
                json={"reason": "same competency", "payload": {"skill_id": "SKILL_PYTHON"}}, headers=auth_headers("reviewer"))
    result = client.get("/api/v1/jds/FACT1/normalized-result", headers=headers).json()["data"]
    skill = result["normalized_requirements"][0]["normalized_skills"][0]
    assert skill["skill_id"] == "SKILL_PYTHON"
    assert skill["resolution_status"] == "manually_confirmed"


def test_structured_skill_drives_api_normalization_and_graph_build(
    client, db, auth_headers
):
    headers = auth_headers()
    prepare_catalog(db)
    db.add(SkillCategory(code="DEVOPS", name="DevOps"))
    db.add(Skill(
        skill_id="SKILL_DOCKER", canonical_name="Docker", category_code="DEVOPS"
    ))
    db.commit()
    prepare_jd(client, headers, doc_id="CONSISTENT1", skill="Python")
    record = db.scalar(select(JDExtractionRecord).where(
        JDExtractionRecord.document_id == "CONSISTENT1"
    ))
    original = deepcopy(record.payload)
    requirement = db.scalar(select(ExtractedCandidateRequirement).where(
        ExtractedCandidateRequirement.document_id == "CONSISTENT1",
        ExtractedCandidateRequirement.requirement_id == "r1",
    ))
    requirement.payload = {**requirement.payload, "items": [{"name": "Docker"}]}
    db.commit()

    extraction = client.get(
        "/api/v1/jds/CONSISTENT1/extraction-result", headers=headers
    ).json()["data"]
    assert extraction["requirements"][0]["items"][0]["name"] == "Docker"
    normalized = client.post(
        "/api/v1/jds/CONSISTENT1/normalize", headers=headers
    ).json()["data"]
    normalized_skill = normalized["normalized_requirements"][0]["normalized_skills"][0]
    assert normalized_skill["source_name"] == "Docker"
    assert normalized_skill["skill_id"] == "SKILL_DOCKER"
    normalized["job_classification"] = {
        "schema_version": "job-position-classification.v3",
        "taxonomy_version": "position-taxonomy.v3.0.0",
        "source_title": "后端工程师",
        "position_code": "BACKEND_ENGINEER",
        "position_name": "后端工程师",
        "family_code": "SOFTWARE_ENGINEERING",
        "family_name": "软件工程与研发",
        "candidate_positions": [
            {"position_code": "BACKEND_ENGINEER", "score": 0.95}
        ],
        "career_level": "mid",
        "leadership_scope": "none",
        "technology_focus_codes": [],
        "industry_context_codes": [],
        "observed_skill_domain_codes": ["software_engineering"],
        "confidence": 0.95,
        "classification_status": "resolved",
        "review_reason_codes": [],
        "evidence_refs": ["r1"],
        "classification_policy_version": "position-classifier.v3.0",
    }
    assert client.post(
        "/api/v1/jds/CONSISTENT1/normalized-result/import",
        json=normalized,
        headers=headers,
    ).status_code == 200

    build = client.post(
        "/api/v1/positions/BACKEND_ENGINEER/graph/build", json={}, headers=headers
    )
    assert build.status_code == 200
    relation = db.scalar(select(PositionSkillRelationDraft).where(
        PositionSkillRelationDraft.build_run_id == build.json()["data"]["build_run_id"]
    ))
    assert relation.skill_id == "SKILL_DOCKER"
    db.refresh(record)
    assert record.payload == original


def test_complete_v2_dto_rebuild_and_missing_projection_error(client, db, auth_headers):
    headers = auth_headers()
    client.post("/api/v1/jds", json={
        "document_id": "COMPLETE1",
        "raw_text": "工程师 开发服务 本科 科技企业 上海",
    }, headers=headers)
    payload = {
        "document_id": "COMPLETE1",
        "job_title": {"text": "工程师", "evidence": {
            "source_id": "COMPLETE1", "quote": "工程师"}},
        "responsibilities": [{"requirement_id": "t1", "text": "开发服务",
            "evidence": {"source_id": "COMPLETE1", "quote": "开发服务"}}],
        "requirements": [{"requirement_id": "r1", "kind": "education",
            "modality": "required", "text": "本科",
            "evidence": {"source_id": "COMPLETE1", "quote": "本科"}}],
        "company_facts": [{"fact_id": "c1", "text": "科技企业",
            "evidence": {"source_id": "COMPLETE1", "quote": "科技企业"}}],
        "employment_facts": [{"fact_id": "e1", "fact_type": "location",
            "text": "上海", "evidence": {
                "source_id": "COMPLETE1", "quote": "上海"}}],
    }
    assert client.post(
        "/api/v1/jds/COMPLETE1/extraction-result/import",
        json=payload, headers=headers,
    ).status_code == 200
    rebuilt = client.get(
        "/api/v1/jds/COMPLETE1/extraction-result", headers=headers
    ).json()["data"]
    assert rebuilt == JDExtractionResult.model_validate(payload).model_dump(mode="json")

    title = db.scalar(select(ExtractedJobTitle).where(
        ExtractedJobTitle.document_id == "COMPLETE1"
    ))
    db.delete(title)
    db.commit()
    extraction = client.get(
        "/api/v1/jds/COMPLETE1/extraction-result", headers=headers
    )
    normalization = client.post(
        "/api/v1/jds/COMPLETE1/normalize", headers=headers
    )
    assert extraction.status_code == normalization.status_code == 409
    assert "structured extraction facts missing" in extraction.json()["message"]
