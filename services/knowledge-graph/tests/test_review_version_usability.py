from sqlalchemy import select

from app.models import AuditLog, GraphVersion, PositionSkillRelationDraft, ReviewTask
from tests.factories import approve_build_tasks, prepare_catalog, prepare_jd


def _open_build(client, db, headers):
    prepare_catalog(db)
    prepare_jd(client, headers)
    response = client.post(
        "/api/v1/positions/BACKEND_ENGINEER/graph/build", json={}, headers=headers
    )
    assert response.status_code == 200
    return response.json()["data"]


def test_review_task_read_model_and_action_feedback(client, db, auth_headers):
    admin = auth_headers()
    reviewer = auth_headers("reviewer")
    build = _open_build(client, db, admin)
    tasks = client.get("/api/v1/review-tasks", headers=reviewer).json()["data"]
    task = next(
        item
        for item in tasks
        if item["build_run_id"] == build["build_run_id"]
        and item["object_type"] == "position_skill_relation"
    )
    assert set(task) >= {
        "original_values",
        "current_values",
        "modified_values",
        "evidence",
        "impacted_relations",
        "risk_level",
    }
    assert task["original_values"]["weight"] >= 0
    assert task["current_values"]["revision"] == 1
    assert task["evidence"][0]["evidence"]["alignment"] == "exact"
    assert task["impacted_relations"][0]["skill_name"] == "Python"
    assert task["risk_level"] in {"low", "medium"}

    claimed = client.post(
        f'/api/v1/review-tasks/{task["id"]}/claim',
        json={"reason": "take ownership"},
        headers=reviewer,
    ).json()["data"]
    assert claimed["status"] == "claimed"
    assert claimed["feedback"] == "review task claimed"
    assert set(claimed["allowed_actions"]) == {"approve", "reject", "modify"}

    modified = client.post(
        f'/api/v1/review-tasks/{task["id"]}/modify',
        json={
            "reason": "record proposed values",
            "payload": {
                "expected_revision": 1,
                "weight": 0.81,
                "confidence": 0.88,
                "importance_level": "core",
            },
        },
        headers=reviewer,
    )
    assert modified.status_code == 200, modified.text
    modified = modified.json()["data"]
    assert modified["status"] == "modified"
    relation = db.get(
        PositionSkillRelationDraft, task["impacted_relations"][0]["relation_id"]
    )
    db.refresh(relation)
    assert relation.revision == 2
    assert relation.final_weight == 0.81
    assert relation.final_confidence == 0.88
    assert relation.final_importance_level == "core"
    refreshed = client.get("/api/v1/review-tasks", headers=reviewer).json()["data"]
    current = next(item for item in refreshed if item["id"] == task["id"])
    assert current["modified_values"] == {
        "weight": 0.81,
        "confidence": 0.88,
        "importance_level": "core",
    }
    assert current["current_values"]["revision"] == 2
    assert current["allowed_actions"] == ["approve", "reject"]
    explanation = client.get(
        f"/api/v1/relations/{relation.id}/explanation"
    ).json()["data"]
    edit_history = explanation["manual_modification_history"][-1]
    assert edit_history["actor_id"] is not None
    assert edit_history["reason"] == "record proposed values"
    assert edit_history["modification_source"] == "review_task"
    assert edit_history["review_task_id"] == task["id"]

    approved = client.post(
        f'/api/v1/review-tasks/{task["id"]}/approve',
        json={"reason": "evidence verified"},
        headers=reviewer,
    ).json()["data"]
    assert approved["status"] == "approved"
    assert approved["allowed_actions"] == []

    rejected_task = ReviewTask(object_type="evidence", object_id="external")
    db.add(rejected_task)
    db.commit()
    client.post(
        f"/api/v1/review-tasks/{rejected_task.id}/claim",
        json={"reason": "inspect"},
        headers=reviewer,
    )
    rejected = client.post(
        f"/api/v1/review-tasks/{rejected_task.id}/reject",
        json={"reason": "insufficient support"},
        headers=reviewer,
    ).json()["data"]
    assert rejected["status"] == "rejected"
    assert rejected["feedback"] == "review task rejected"

    audits = db.scalars(
        select(AuditLog).where(
            AuditLog.object_id == str(task["id"]),
            AuditLog.object_type == "review_task",
        ).order_by(AuditLog.id)
    ).all()
    assert [item.action for item in audits] == [
        "review_claim", "review_modify", "review_approve"
    ]
    assert all(item.actor_id is not None and item.created_at is not None for item in audits)
    assert audits[0].reason == "take ownership"
    assert audits[-1].reason == "evidence verified"
    assert audits[-1].before_snapshot["status"] == "modified"
    assert audits[-1].after_snapshot["status"] == "approved"


def test_review_task_detail_by_id_and_missing_404(client, db, auth_headers):
    admin = auth_headers()
    reviewer = auth_headers("reviewer")
    _open_build(client, db, admin)
    tasks = client.get(
        "/api/v1/review-tasks?page_size=100", headers=reviewer
    ).json()["data"]
    assert tasks
    task = tasks[0]

    detail = client.get(
        f'/api/v1/review-tasks/{task["id"]}', headers=reviewer
    )
    assert detail.status_code == 200
    assert detail.json()["data"]["id"] == task["id"]
    assert detail.json()["data"]["task_id"] == str(task["id"])
    assert detail.json()["data"]["status"] == task["status"]

    missing = client.get(
        "/api/v1/review-tasks/999999", headers=reviewer
    )
    assert missing.status_code == 404


def test_review_modify_revision_conflict_and_task_paging(client, db, auth_headers):
    admin = auth_headers()
    reviewer = auth_headers("reviewer")
    build = _open_build(client, db, admin)
    response = client.get(
        "/api/v1/review-tasks?page=1&page_size=1&status=pending",
        headers=reviewer,
    )
    assert response.status_code == 200
    assert len(response.json()["data"]) == 1
    assert int(response.headers["X-Total-Count"]) >= 1
    assert response.headers["X-Page"] == "1"
    assert response.headers["X-Page-Size"] == "1"
    risk = response.json()["data"][0]["risk_level"]
    assert client.get(
        f"/api/v1/review-tasks?risk_level={risk}", headers=reviewer
    ).status_code == 200

    task = next(
        item for item in client.get(
            "/api/v1/review-tasks?page_size=100", headers=reviewer
        ).json()["data"]
        if item["build_run_id"] == build["build_run_id"]
        and item["object_type"] == "position_skill_relation"
    )
    relation = db.get(PositionSkillRelationDraft, task["impacted_relations"][0]["relation_id"])
    client.post(
        f'/api/v1/review-tasks/{task["id"]}/claim',
        json={"reason": "claim for conflict test"}, headers=reviewer,
    )
    edited = client.post(
        f"/api/v1/relations/{relation.id}/modify",
        json={
            "build_run_id": relation.build_run_id,
            "position_id": relation.position_id,
            "expected_revision": 1,
            "weight": 0.73,
            "reason": "parallel expert edit",
        },
        headers=admin,
    )
    assert edited.status_code == 200
    conflict = client.post(
        f'/api/v1/review-tasks/{task["id"]}/modify',
        json={
            "reason": "stale review edit",
            "payload": {"expected_revision": 1, "weight": 0.8},
        },
        headers=reviewer,
    )
    assert conflict.status_code == 409
    assert conflict.json()["details"]["current_revision"] == 2
    db.refresh(relation)
    assert relation.revision == 2 and relation.final_weight == 0.73
    db.refresh(db.get(ReviewTask, task["id"]))
    assert db.get(ReviewTask, task["id"]).status == "claimed"


def test_relation_edit_validation_and_revision_conflict_feedback(
    client, db, auth_headers
):
    headers = auth_headers()
    build = _open_build(client, db, headers)
    relation = db.scalar(
        select(PositionSkillRelationDraft).where(
            PositionSkillRelationDraft.build_run_id == build["build_run_id"]
        )
    )
    url = f"/api/v1/relations/{relation.id}/modify"
    base = {
        "build_run_id": build["build_run_id"],
        "position_id": "BACKEND_ENGINEER",
        "expected_revision": 1,
        "reason": "review correction",
    }
    assert client.post(url, json={**base, "weight": -0.01}, headers=headers).status_code == 422
    assert client.post(url, json={**base, "confidence": 1.01}, headers=headers).status_code == 422
    assert client.post(
        url, json={**base, "importance_level": "critical"}, headers=headers
    ).status_code == 422
    assert client.post(
        url, json={**base, "weight": 0.8, "reason": "   "}, headers=headers
    ).status_code == 422
    saved = client.post(
        url, json={**base, "weight": 0.8}, headers=headers
    )
    assert saved.status_code == 200
    conflict = client.post(
        url, json={**base, "confidence": 0.9}, headers=headers
    )
    assert conflict.status_code == 409
    assert conflict.json()["details"] == {
        "current_revision": 2,
        "error_code": "RELATION_EDIT_CONFLICT",
    }
    assert "reload current revision 2" in conflict.json()["message"]


def test_rollback_creates_new_version_and_exposes_source_reason(
    client, db, auth_headers
):
    headers = auth_headers()
    first_build = _open_build(client, db, headers)
    approve_build_tasks(client, first_build["build_run_id"], headers)
    first = client.post(
        f'/api/v1/graph/build-runs/{first_build["build_run_id"]}/publish',
        json={"reason": "first release"},
        headers=headers,
    ).json()["data"]
    second_build = client.post(
        "/api/v1/positions/BACKEND_ENGINEER/graph/build", json={}, headers=headers
    ).json()["data"]
    approve_build_tasks(client, second_build["build_run_id"], headers)
    second = client.post(
        f'/api/v1/graph/build-runs/{second_build["build_run_id"]}/publish',
        json={"reason": "second release"},
        headers=headers,
    ).json()["data"]
    source = db.get(GraphVersion, first["version_id"])
    source_version = source.source_version
    source_snapshot = source.snapshot

    path = (
        "/api/v1/positions/BACKEND_ENGINEER/graph/versions/"
        f'{first["version_id"]}/rollback'
    )
    assert client.post(
        path, json={"reason": "   "}, headers=headers
    ).status_code == 422
    rolled = client.post(
        path, json={"reason": "restore verified baseline"}, headers=headers
    )
    assert rolled.status_code == 200
    result = rolled.json()["data"]
    assert result["version_number"] == 3
    assert result["rollback_from_version_id"] == first["version_id"]

    db.expire_all()
    assert db.get(GraphVersion, first["version_id"]).source_version == source_version
    assert db.get(GraphVersion, first["version_id"]).snapshot == source_snapshot
    assert db.get(GraphVersion, second["version_id"]).version_number == 2
    versions = client.get(
        "/api/v1/positions/BACKEND_ENGINEER/graph/versions"
    ).json()["data"]
    rollback_version = next(item for item in versions if item["id"] == result["version_id"])
    assert rollback_version["rollback_from_version_number"] == 1
    assert rollback_version["rollback_reason"] == "restore verified baseline"
    assert rollback_version["is_current"] is True
    detail = client.get(
        f'/api/v1/positions/BACKEND_ENGINEER/graph/versions/{result["version_id"]}'
    ).json()["data"]
    assert detail["rollback_from_version_number"] == 1
    assert detail["rollback_reason"] == "restore verified baseline"
    assert detail["is_current"] is True
    rollback_diff = client.get(
        "/api/v1/positions/BACKEND_ENGINEER/graph/versions/diff"
        f'?from_version_id={second["version_id"]}&to_version_id={result["version_id"]}'
    ).json()["data"]
    assert rollback_diff["rollback_source"]["source_version_id"] == first["version_id"]
    assert rollback_diff["rollback_source"]["rollback_reason"] == "restore verified baseline"


def test_published_manual_edit_appears_in_version_detail_and_diff(
    client, db, auth_headers
):
    headers = auth_headers()
    first_build = _open_build(client, db, headers)
    approve_build_tasks(client, first_build["build_run_id"], headers)
    first = client.post(
        f'/api/v1/graph/build-runs/{first_build["build_run_id"]}/publish',
        json={"reason": "baseline"}, headers=headers,
    ).json()["data"]
    second_build = client.post(
        "/api/v1/positions/BACKEND_ENGINEER/graph/build", json={}, headers=headers
    ).json()["data"]
    relation = db.scalar(select(PositionSkillRelationDraft).where(
        PositionSkillRelationDraft.build_run_id == second_build["build_run_id"]
    ))
    edited = client.post(
        f"/api/v1/relations/{relation.id}/modify",
        json={
            "build_run_id": relation.build_run_id,
            "position_id": relation.position_id,
            "expected_revision": relation.revision,
            "weight": 0.77,
            "confidence": 0.91,
            "importance_level": "core",
            "reason": "expert calibration",
        },
        headers=headers,
    )
    assert edited.status_code == 200
    approve_build_tasks(client, second_build["build_run_id"], headers)
    second = client.post(
        f'/api/v1/graph/build-runs/{second_build["build_run_id"]}/publish',
        json={"reason": "publish calibrated values"}, headers=headers,
    ).json()["data"]
    detail = client.get(
        f'/api/v1/positions/BACKEND_ENGINEER/graph/versions/{second["version_id"]}'
    ).json()["data"]
    snapshot_relation = detail["snapshot"]["skill_relations"][0]
    assert snapshot_relation["final_weight"] == 0.77
    assert snapshot_relation["final_confidence"] == 0.91
    assert snapshot_relation["final_importance_level"] == "core"
    diff = client.get(
        "/api/v1/positions/BACKEND_ENGINEER/graph/versions/diff"
        f'?from_version_id={first["version_id"]}&to_version_id={second["version_id"]}'
    ).json()["data"]
    change = diff["changed"][0]
    assert "manual_modification" in change["change_sources"]
    assert change["business_changes"]["weight"]["manual_weight"]["after"] == 0.77


def test_relation_edit_openapi_declares_ranges_and_importance_enum(
    client, auth_headers
):
    schema = client.get("/api/v1/openapi.json").json()["components"]["schemas"][
        "RelationModify"
    ]["properties"]
    assert schema["weight"]["anyOf"][0]["minimum"] == 0.0
    assert schema["weight"]["anyOf"][0]["maximum"] == 1.0
    assert schema["confidence"]["anyOf"][0]["minimum"] == 0.0
    assert schema["confidence"]["anyOf"][0]["maximum"] == 1.0
    assert schema["importance_level"]["anyOf"][0]["enum"] == [
        "core",
        "important",
        "supplementary",
    ]
    review_path = client.get("/api/v1/openapi.json").json()["paths"][
        "/api/v1/review-tasks"
    ]["get"]
    parameters = {item["name"]: item for item in review_path["parameters"]}
    assert set(parameters) == {
        "page",
        "page_size",
        "status",
        "task_kind",
        "risk_level",
        "build_run_id",
    }
    assert parameters["page_size"]["schema"]["maximum"] == 100
    review_response = review_path["responses"]["200"]
    assert review_response["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ReviewTaskListEnvelope"
    }
    assert set(review_response["headers"]) == {
        "X-Total-Count", "X-Page", "X-Page-Size"
    }
    modify_schema = client.get("/api/v1/openapi.json").json()["components"][
        "schemas"
    ]["ReviewModifyPayload"]["properties"]
    assert modify_schema["expected_revision"]["anyOf"][0]["minimum"] == 1
    assert modify_schema["weight"]["anyOf"][0]["maximum"] == 1.0
    invalid_review_edit = client.post(
        "/api/v1/review-tasks/1/modify",
        json={
            "reason": "invalid range",
            "payload": {"expected_revision": 1, "confidence": 1.01},
        },
        headers=auth_headers("reviewer"),
    )
    assert invalid_review_edit.status_code == 422
