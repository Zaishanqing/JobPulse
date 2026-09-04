from sqlalchemy import select

from app.models import ExtractionEvidence, GraphBuildRun, ReviewTask
from tests.factories import prepare_catalog, prepare_jd, valid_build

def rules(response): return {item["rule"] for item in response.json()["details"]["errors"]}

def test_empty_and_non_succeeded_builds_cannot_publish(client,db,auth_headers):
    headers=auth_headers(); prepare_catalog(db)
    empty=client.post("/api/v1/positions/BACKEND_ENGINEER/graph/build",json={},headers=headers).json()["data"]
    rejected=client.post(f'/api/v1/graph/build-runs/{empty["build_run_id"]}/publish',json={},headers=headers)
    assert rejected.status_code==409 and "non_empty_graph" in rules(rejected)
    pending=GraphBuildRun(position_id="BACKEND_ENGINEER",status="pending",config_snapshot={"minimum_valid_samples":1},summary={}); db.add(pending); db.commit()
    rejected=client.post(f"/api/v1/graph/build-runs/{pending.id}/publish",json={},headers=headers)
    assert rejected.status_code==409 and "build_status" in rules(rejected)

def test_sample_open_task_and_evidence_gates(client,db,auth_headers):
    headers=auth_headers(); prepare_catalog(db); prepare_jd(client,headers)
    build=client.post("/api/v1/positions/BACKEND_ENGINEER/graph/build",json={"minimum_valid_samples":2},headers=headers).json()["data"]
    rejected=client.post(f'/api/v1/graph/build-runs/{build["build_run_id"]}/publish',json={},headers=headers)
    assert "minimum_valid_samples" in rules(rejected)
    run=db.get(GraphBuildRun,build["build_run_id"]); run.config_snapshot={**run.config_snapshot,"minimum_valid_samples":1}; db.add(ReviewTask(object_type="evidence",object_id="1",build_run_id=run.id)); db.commit()
    rejected=client.post(f"/api/v1/graph/build-runs/{run.id}/publish",json={},headers=headers)
    assert "open_review_tasks" in rules(rejected)
    db.query(ReviewTask).delete(); evidence=db.scalar(select(ExtractionEvidence).where(ExtractionEvidence.owner_ref=="r1")); evidence.alignment="normalized_exact"; db.commit()
    rejected=client.post(f"/api/v1/graph/build-runs/{run.id}/publish",json={},headers=headers)
    assert "support_integrity" in rules(rejected)

def test_legal_graph_publishes(client,db,auth_headers):
    headers=auth_headers(); build=valid_build(client,db,headers)
    assert client.post(f'/api/v1/graph/build-runs/{build["build_run_id"]}/publish',json={},headers=headers).status_code==200
