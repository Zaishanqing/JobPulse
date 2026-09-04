from sqlalchemy import select

from app.models import PositionSkillRelationDraft, PositionSkillSupport
from tests.factories import prepare_catalog, prepare_jd

def test_document_skill_uses_highest_modality_but_keeps_all_evidence(client,db,auth_headers):
    headers=auth_headers(); prepare_catalog(db)
    quote="熟悉 Python"; raw=f"后端工程师\n{quote}\nPython 可加分"
    client.post("/api/v1/jds",json={"document_id":"JD1","raw_text":raw},headers=headers)
    payload={"document_id":"JD1","job_title":{"text":"后端工程师","evidence":{"source_id":"JD1","quote":"后端工程师"}},"responsibilities":[],"requirements":[
        {"requirement_id":"required","kind":"skill","modality":"required","evidence":{"source_id":"JD1","quote":quote},"items":[{"name":"Python"}]},
        {"requirement_id":"bonus","kind":"skill","modality":"bonus","evidence":{"source_id":"JD1","quote":"Python 可加分"},"items":[{"name":"Python"}]}],"company_facts":[],"employment_facts":[]}
    for path,body in (("extraction-result/import",payload),("extraction-result/align",None)):
        response=client.post(f"/api/v1/jds/JD1/{path}",json=body,headers=headers); assert response.status_code==200
    normalized=client.post("/api/v1/jds/JD1/normalize",headers=headers).json()["data"]
    normalized["job_classification"]={
        "schema_version":"job-position-classification.v3",
        "taxonomy_version":"position-taxonomy.v3.0.0",
        "source_title":"后端工程师",
        "position_code":"BACKEND_ENGINEER",
        "position_name":"后端工程师",
        "family_code":"SOFTWARE_ENGINEERING",
        "family_name":"软件工程与研发",
        "candidate_positions":[{"position_code":"BACKEND_ENGINEER","score":0.95}],
        "career_level":"mid",
        "leadership_scope":"none",
        "technology_focus_codes":[],
        "industry_context_codes":[],
        "observed_skill_domain_codes":["software_engineering"],
        "confidence":0.95,
        "classification_status":"resolved",
        "review_reason_codes":[],
        "evidence_refs":["required","bonus"],
        "classification_policy_version":"position-classifier.v3.0",
    }
    assert client.post("/api/v1/jds/JD1/normalized-result/import",json=normalized,headers=headers).status_code==200
    assert client.post("/api/v1/jds/JD1/duplicate-check",json={},headers=headers).status_code==200
    build=client.post("/api/v1/positions/BACKEND_ENGINEER/graph/build",json={},headers=headers); assert build.status_code==200
    relation=db.scalar(select(PositionSkillRelationDraft)); supports=db.scalars(select(PositionSkillSupport)).all()
    assert relation.metrics["support_document_count"]==1
    assert relation.metrics["modality_distribution"]["required"]==1
    assert len(supports)==2 and relation.metrics["support_count"]==2
