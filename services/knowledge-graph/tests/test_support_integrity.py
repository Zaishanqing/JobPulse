import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.models import (ExtractedCandidateRequirement, ExtractionEvidence, GraphBuildRun, JDDocument,
    NormalizedSkillRecord, PositionSkillSupport)
from tests.factories import valid_build

def test_sqlite_foreign_keys_and_forged_ids_are_rejected(client,db,auth_headers,users):
    valid_build(client,db,auth_headers()); support=db.scalar(select(PositionSkillSupport))
    assert db.execute(text("PRAGMA foreign_keys")).scalar_one()==1
    for attribute,value in (("skill_id","SKILL_FAKE"),("normalized_skill_id",999999),("evidence_id",999999)):
        original=getattr(support,attribute); setattr(support,attribute,value)
        with pytest.raises(IntegrityError): db.commit()
        db.rollback(); support=db.get(PositionSkillSupport,support.id); assert getattr(support,attribute)==original

def test_defensive_publish_revalidates_support_chain(client,db,auth_headers,users):
    build=valid_build(client,db,auth_headers()); support=db.scalar(select(PositionSkillSupport)); run=db.get(GraphBuildRun,build["build_run_id"])
    normalized=db.get(NormalizedSkillRecord,support.normalized_skill_id); normalized.resolution_status="unresolved"; db.commit()
    response=client.post(f'/api/v1/graph/build-runs/{run.id}/publish',json={},headers=auth_headers())
    assert response.status_code==409
    assert any(x["message"]=="normalized_skill_unresolved" for x in response.json()["details"]["errors"])
    normalized.resolution_status="resolved"; source=db.get(ExtractedCandidateRequirement,support.source_requirement_id); source.kind="education"; db.commit()
    response=client.post(f'/api/v1/graph/build-runs/{run.id}/publish',json={},headers=auth_headers())
    assert response.status_code==409
    assert any(x["message"]=="source_requirement_not_skill" for x in response.json()["details"]["errors"])

def test_wrong_document_evidence_is_rejected(client,db,auth_headers,users):
    build=valid_build(client,db,auth_headers()); support=db.scalar(select(PositionSkillSupport)); evidence=db.get(ExtractionEvidence,support.evidence_id)
    db.add(JDDocument(document_id="OTHER",raw_text=evidence.quote)); db.flush(); evidence.document_id="OTHER"; db.commit()
    response=client.post(f'/api/v1/graph/build-runs/{build["build_run_id"]}/publish',json={},headers=auth_headers())
    assert response.status_code==409 and any(x.get("message")=="evidence_wrong_document" for x in response.json()["details"]["errors"])
