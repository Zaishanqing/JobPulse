from sqlalchemy import select

from app.models import ExtractionEvidence, ReviewTask
from tests.factories import prepare_catalog, prepare_jd


def build_with_profile(client, db, auth_headers):
    headers = auth_headers()
    prepare_catalog(db)
    prepare_jd(client, headers)
    response = client.post(
        "/api/v1/positions/BACKEND_ENGINEER/graph/build",
        json={},
        headers=headers,
    )
    assert response.status_code == 200
    return response.json()["data"]["build_run_id"]


def tasks_for(db, build_run_id, object_type):
    return db.scalars(
        select(ReviewTask).where(
            ReviewTask.build_run_id == build_run_id,
            ReviewTask.object_type == object_type,
        )
    ).all()


def test_review_tasks_for_evidence(client, db, auth_headers):
    headers = auth_headers()
    prepare_catalog(db)
    prepare_jd(client, headers)
    evidence = db.scalar(
        select(ExtractionEvidence).where(ExtractionEvidence.owner_ref == "t1")
    )
    evidence.alignment = "unresolved"
    evidence.start = None
    evidence.end = None
    db.commit()

    build = client.post(
        "/api/v1/positions/BACKEND_ENGINEER/graph/build", json={}, headers=headers
    ).json()["data"]
    tasks = tasks_for(db, build["build_run_id"], "evidence_alignment")

    matching = [task for task in tasks if task.object_id == str(evidence.id)]
    assert len(matching) == 1
    task = matching[0]
    assert task.build_run_id == build["build_run_id"]
    assert task.object_type == "evidence_alignment"
    assert task.object_id == str(evidence.id)
    assert task.payload["reason"] == "alignment_not_exact"
    assert task.payload["reasons"] == [
        "alignment_not_exact",
        "quote_coordinates_invalid",
    ]


def test_review_tasks_for_requirements(client, db, auth_headers):
    run_id = build_with_profile(client, db, auth_headers)
    tasks = tasks_for(db, run_id, "position_requirement")
    assert tasks
    assert all(task.object_id and task.payload["reasons"] for task in tasks)


def test_review_tasks_for_tasks(client, db, auth_headers):
    run_id = build_with_profile(client, db, auth_headers)
    tasks = tasks_for(db, run_id, "position_task")
    assert len(tasks) == 1
    assert tasks[0].payload["reason"] == "low_confidence_merge"


def test_build_review_task_object_types_and_open_dedup(client, db, auth_headers):
    run_id = build_with_profile(client, db, auth_headers)
    object_types = {
        task.object_type
        for task in db.scalars(
            select(ReviewTask).where(ReviewTask.build_run_id == run_id)
        ).all()
    }
    assert {
        "position_skill_relation",
        "position_task",
        "graph_version",
    } <= object_types
    tasks = db.scalars(
        select(ReviewTask).where(ReviewTask.build_run_id == run_id)
    ).all()
    open_keys = [
        (task.object_type, task.object_id)
        for task in tasks
        if task.status in ("pending", "claimed", "modified")
    ]
    assert len(open_keys) == len(set(open_keys))
