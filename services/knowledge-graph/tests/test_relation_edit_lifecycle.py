from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.application.use_cases import ModifyRelationUseCase
from app.domain.write_models import RelationModification
from app.domain.relation_editing import RelationEditFacts, RelationEditResult
from app.domain.review_tasks import ReviewTaskDedupFacts, ReviewTaskResult
from app.infrastructure.sqlalchemy.query_service import (
    SqlAlchemyKnowledgeGraphQueryService,
)
from app.main import create_app
from app.models import AuditLog, GraphVersion, PositionSkillRelationDraft, ReviewTask
from tests.factories import approve_build_tasks
from tests.test_graph import prepare_catalog, prepare_jd


def _publish_v1_as_important(client, db, headers):
    prepare_catalog(db)
    prepare_jd(client, headers)
    build = client.post(
        "/api/v1/positions/POS_BACKEND/graph/build", json={}, headers=headers
    ).json()["data"]
    relation = db.scalar(select(PositionSkillRelationDraft))
    relation.auto_importance_level = "important"
    relation.final_importance_level = "important"
    db.commit()
    approve_build_tasks(client, build["build_run_id"], headers)
    published = client.post(
        f'/api/v1/graph/build-runs/{build["build_run_id"]}/publish',
        json={}, headers=headers,
    ).json()["data"]
    return build, relation, published


def test_published_to_draft_edit_publish_is_persistent_and_versioned(
    client, db, auth_headers
):
    headers = auth_headers()
    build, published_relation, v1_data = _publish_v1_as_important(
        client, db, headers
    )
    v1 = db.get(GraphVersion, v1_data["version_id"])

    immutable = client.post(
        f"/api/v1/relations/{published_relation.id}/modify",
        json={
            "build_run_id": build["build_run_id"],
            "position_id": "POS_BACKEND",
            "expected_revision": published_relation.revision,
            "importance_level": "core",
            "reason": "must not mutate V1",
        },
        headers=headers,
    )
    assert immutable.status_code == 409
    assert v1.snapshot["skill_relations"][0]["importance_level"] == "important"

    opened = client.post(
        "/api/v1/positions/POS_BACKEND/graph/drafts",
        json={"base_version_id": v1.id}, headers=headers,
    )
    assert opened.status_code == 200
    opened_data = opened.json()["data"]
    assert set(opened_data) >= {"draft_id", "build_run_id"}
    draft_id = opened_data["draft_id"]
    build_run_id = opened_data["build_run_id"]
    draft = client.get(
        f"/api/v1/graph/build-runs/{build_run_id}/graph", headers=headers
    ).json()["data"]
    draft_relation = draft["skill_relations"][0]
    assert draft["view_type"] == "draft"
    assert draft_relation["importance_level"] == "important"

    modified = client.post(
        f'/api/v1/relations/{draft_relation["relation_id"]}/modify',
        json={
            "build_run_id": build_run_id,
            "position_id": "POS_BACKEND",
            "expected_revision": draft_relation["revision"],
            "weight": draft_relation["weight"],
            "confidence": draft_relation["confidence"],
            "importance_level": "core",
            "reason": "expert override",
        },
        headers=headers,
    )
    assert modified.status_code == 200
    result = modified.json()["data"]
    assert result["relation_id"] == draft_relation["relation_id"]
    assert result["draft_id"] == draft_id
    assert result["auto_importance_level"] == "important"
    assert result["manual_importance_level"] == "core"
    assert result["importance_level"] == "core"
    assert result["revision"] == draft_relation["revision"] + 1

    refreshed = client.get(
        f"/api/v1/graph/build-runs/{build_run_id}/graph", headers=headers
    ).json()["data"]
    assert refreshed["skill_relations"][0]["importance_level"] == "core"
    assert client.get(
        "/api/v1/positions/POS_BACKEND/graph"
    ).json()["data"]["skill_relations"][0]["importance_level"] == "important"

    independent_session = sessionmaker(bind=db.get_bind(), expire_on_commit=False)()
    try:
        independent = SqlAlchemyKnowledgeGraphQueryService(independent_session)
        assert independent.draft_graph(build_run_id)["skill_relations"][0][
            "importance_level"
        ] == "core"
    finally:
        independent_session.close()

    restarted = create_app()
    restarted.state.request_session_factory = lambda: db
    restarted.state.close_request_sessions = False
    with TestClient(restarted) as restarted_client:
        persisted = restarted_client.get(
            f"/api/v1/graph/build-runs/{build_run_id}/graph", headers=headers
        )
        assert persisted.json()["data"]["skill_relations"][0][
            "importance_level"
        ] == "core"

    approve_build_tasks(client, build_run_id, headers)
    v2_data = client.post(
        f"/api/v1/graph/build-runs/{build_run_id}/publish",
        json={}, headers=headers,
    ).json()["data"]
    v2 = db.get(GraphVersion, v2_data["version_id"])
    assert v1.snapshot["skill_relations"][0]["importance_level"] == "important"
    assert v2.snapshot["skill_relations"][0]["importance_level"] == "core"
    latest = client.get("/api/v1/positions/POS_BACKEND/graph").json()["data"]
    assert latest["skill_relations"][0]["importance_level"] == "core"

    diff = client.get(
        "/api/v1/positions/POS_BACKEND/graph/versions/diff",
        params={"from_version_id": v1.id, "to_version_id": v2.id},
    ).json()["data"]
    assert len(diff["changed"]) == 1
    assert diff["changed"][0]["skill_id"] == "SKILL_PYTHON"
    assert diff["changed"][0]["changed_fields"]["importance_level"] == {
        "before": "important", "after": "core"
    }
    assert "relation_id" not in diff["changed"][0]["changed_fields"]
    assert diff["context_changes"] == {}


def test_relation_modify_requires_matching_explicit_draft_context(
    client, db, auth_headers
):
    headers = auth_headers()
    _, _, v1 = _publish_v1_as_important(client, db, headers)
    draft_id = client.post(
        "/api/v1/positions/POS_BACKEND/graph/drafts",
        json={"base_version_id": v1["version_id"]}, headers=headers,
    ).json()["data"]["draft_id"]
    relation = db.scalar(select(PositionSkillRelationDraft).where(
        PositionSkillRelationDraft.build_run_id == draft_id
    ))
    assert client.post(
        f"/api/v1/relations/{relation.id}/modify",
        json={"importance_level": "core", "reason": "missing context"},
        headers=headers,
    ).status_code == 422
    assert client.post(
        f"/api/v1/relations/{relation.id}/modify",
        json={
                "build_run_id": draft_id + 1,
                "position_id": "POS_BACKEND",
                "expected_revision": relation.revision,
            "importance_level": "core",
            "reason": "wrong draft",
        }, headers=headers,
    ).status_code == 409


def test_commit_failure_rolls_back_and_cannot_return_success():
    state = SimpleNamespace(rolled_back=False)

    class FailingUow:
        graph_drafts = SimpleNamespace(
            load_relation_edit_facts=lambda _relation_id: RelationEditFacts(
                3, True, 8, "POS_BACKEND", True, "POS_BACKEND", False,
                1, "approved", 0.5, None, 0.5, 0.8, None, 0.8,
                "important", None, "important",
            ),
            apply_relation_edit_plan=lambda _plan: RelationEditResult(
                3, 8, 8, "POS_BACKEND", "important", None, "important",
                0.5, 0.8, 0.5, None, 0.8, None, 2,
            ),
        )
        review_tasks = SimpleNamespace(
            load_review_task_dedup_facts=lambda key: ReviewTaskDedupFacts(key, ()),
            save_new_task=lambda _plan: ReviewTaskResult(1, "pending"),
        )
        audits = SimpleNamespace(record=lambda _record: None)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, _exc, _traceback):
            if exc_type is not None:
                state.rolled_back = True

        def commit(self):
            raise RuntimeError("commit failed")

    with pytest.raises(RuntimeError, match="commit failed"):
        ModifyRelationUseCase(FailingUow).execute(
            3,
            RelationModification(8, "POS_BACKEND", 1, "test"),
            1,
            "req_test",
        )
    assert state.rolled_back


def test_rebuild_carries_manual_importance_override(client, db, auth_headers):
    headers = auth_headers()
    _, _, v1 = _publish_v1_as_important(client, db, headers)
    draft_id = client.post(
        "/api/v1/positions/POS_BACKEND/graph/drafts",
        json={"base_version_id": v1["version_id"]}, headers=headers,
    ).json()["data"]["draft_id"]
    relation = db.scalar(select(PositionSkillRelationDraft).where(
        PositionSkillRelationDraft.build_run_id == draft_id
    ))
    client.post(
        f"/api/v1/relations/{relation.id}/modify",
        json={
            "build_run_id": draft_id, "position_id": "POS_BACKEND",
            "expected_revision": relation.revision,
            "importance_level": "core", "reason": "keep this override",
        }, headers=headers,
    )
    rebuilt = client.post(
        "/api/v1/positions/POS_BACKEND/graph/build", json={}, headers=headers
    ).json()["data"]
    carried = db.scalar(select(PositionSkillRelationDraft).where(
        PositionSkillRelationDraft.build_run_id == rebuilt["build_run_id"]
    ))
    # A fresh rebuild recalculates auto values from current evidence.
    assert carried.auto_importance_level == "core"
    assert carried.manual_importance_level == "core"
    assert carried.final_importance_level == "core"
    assert db.scalar(select(ReviewTask).where(
        ReviewTask.build_run_id == rebuilt["build_run_id"]
    )) is not None


@pytest.mark.parametrize(
    ("initial_status", "expected_count", "expected_existing_status"),
    [
        ("pending", 1, "pending"),
        ("claimed", 1, "claimed"),
        ("modified", 1, "modified"),
        ("approved", 2, "approved"),
        ("rejected", 2, "rejected"),
    ],
)
def test_repeated_relation_edit_obeys_review_task_lifecycle(
    client,
    db,
    auth_headers,
    users,
    initial_status,
    expected_count,
    expected_existing_status,
):
    headers = auth_headers()
    prepare_catalog(db)
    prepare_jd(client, headers)
    run_id = client.post(
        "/api/v1/positions/POS_BACKEND/graph/build",
        json={},
        headers=headers,
    ).json()["data"]["build_run_id"]
    relation = db.scalar(
        select(PositionSkillRelationDraft).where(
            PositionSkillRelationDraft.build_run_id == run_id
        )
    )
    existing = db.scalar(
        select(ReviewTask).where(
            ReviewTask.build_run_id == run_id,
            ReviewTask.object_type == "position_skill_relation",
            ReviewTask.object_id == str(relation.id),
        )
    )
    existing.status = initial_status
    existing.assignee_id = (
        users["reviewer"].id if initial_status in {"claimed", "modified"} else None
    )
    existing_id = existing.id
    existing_assignee = existing.assignee_id
    db.commit()

    response = client.post(
        f"/api/v1/relations/{relation.id}/modify",
        json={
            "build_run_id": run_id,
            "position_id": "POS_BACKEND",
            "expected_revision": relation.revision,
            "weight": 0.75,
            "reason": "repeat edit",
        },
        headers=headers,
    )

    assert response.status_code == 200
    tasks = db.scalars(
        select(ReviewTask)
        .where(
            ReviewTask.build_run_id == run_id,
            ReviewTask.object_type == "position_skill_relation",
            ReviewTask.object_id == str(relation.id),
        )
        .order_by(ReviewTask.id)
    ).all()
    assert len(tasks) == expected_count
    assert tasks[0].id == existing_id
    assert tasks[0].status == expected_existing_status
    assert tasks[0].assignee_id == existing_assignee
    if expected_count == 2:
        assert tasks[1].status == "pending"
    audit = db.scalar(
        select(AuditLog)
        .where(
            AuditLog.action == "modify_relation",
            AuditLog.object_id == str(relation.id),
        )
        .order_by(AuditLog.id.desc())
    )
    assert audit.reason == "repeat edit"
