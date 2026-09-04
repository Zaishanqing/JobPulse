from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.auth import create_token, hash_password
from app.config import Settings
from app.database import Base
from app.main import create_app
from app.models import (
    AuditLog, GraphBuildRun, GraphVersion, PositionCategory,
    PositionSkillRelationDraft, Skill, SkillCategory, StandardPosition, User,
)
from tests.factories import approve_build_tasks, prepare_catalog, prepare_jd


def _publish_initial(client, db, headers):
    prepare_catalog(db)
    prepare_jd(client, headers)
    build = client.post(
        "/api/v1/positions/BACKEND_ENGINEER/graph/build", json={}, headers=headers
    ).json()["data"]
    approve_build_tasks(client, build["build_run_id"], headers)
    response = client.post(
        f'/api/v1/graph/build-runs/{build["build_run_id"]}/publish',
        json={}, headers=headers,
    )
    assert response.status_code == 200
    return response.json()["data"]


def _new_build(client, headers):
    result = client.post(
        "/api/v1/positions/BACKEND_ENGINEER/graph/build", json={}, headers=headers
    )
    assert result.status_code == 200
    build = result.json()["data"]
    approve_build_tasks(client, build["build_run_id"], headers)
    return build


def test_stale_draft_publish_is_atomic_and_cannot_create_v3(client, db, auth_headers):
    headers = auth_headers()
    v1 = _publish_initial(client, db, headers)
    build_a = _new_build(client, headers)
    prepare_jd(client, headers, doc_id="JD2")
    build_b = _new_build(client, headers)
    assert db.get(GraphBuildRun, build_a["build_run_id"]).base_version_id == v1["version_id"]
    assert db.get(GraphBuildRun, build_b["build_run_id"]).base_version_id == v1["version_id"]

    published_a = client.post(
        f'/api/v1/graph/build-runs/{build_a["build_run_id"]}/publish',
        json={"release_notes": "A wins"}, headers=headers,
    )
    assert published_a.status_code == 200
    v2_id = published_a.json()["data"]["version_id"]
    v2 = db.get(GraphVersion, v2_id)
    v2_source_version, v2_snapshot = v2.source_version, v2.snapshot
    audit_count = db.scalar(select(func.count(AuditLog.id)))
    b_status = db.get(GraphBuildRun, build_b["build_run_id"]).status

    gate = client.get(
        f'/api/v1/graph/build-runs/{build_b["build_run_id"]}/publish-gate',
        headers=headers,
    )
    assert gate.status_code == 200
    gate_data = gate.json()["data"]
    assert gate_data["allowed"] is False
    assert {
        "rule": "graph_version_current",
        "message": "draft is based on a stale graph version",
    } in gate_data["errors"]

    stale = client.post(
        f'/api/v1/graph/build-runs/{build_b["build_run_id"]}/publish',
        json={"release_notes": "must lose"}, headers=headers,
    )
    assert stale.status_code == 409
    assert stale.json()["details"]["error_code"] == "STALE_GRAPH_DRAFT"
    db.expire_all()
    position = db.scalar(select(StandardPosition).where(
        StandardPosition.position_id == "BACKEND_ENGINEER"
    ))
    assert position.current_version_id == v2_id
    assert db.get(GraphVersion, v2_id).source_version == v2_source_version
    assert db.get(GraphVersion, v2_id).snapshot == v2_snapshot
    assert db.scalar(select(func.count(GraphVersion.id))) == 2
    assert db.scalar(select(func.count(AuditLog.id))) == audit_count
    assert db.get(GraphBuildRun, build_b["build_run_id"]).status == b_status


def test_same_build_sequential_publish_is_idempotent(client, db, auth_headers):
    headers = auth_headers()
    _publish_initial(client, db, headers)
    build = _new_build(client, headers)
    url = f'/api/v1/graph/build-runs/{build["build_run_id"]}/publish'
    first = client.post(url, json={}, headers=headers)
    second = client.post(url, json={}, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json()["data"] == second.json()["data"]
    gate = client.get(
        f'/api/v1/graph/build-runs/{build["build_run_id"]}/publish-gate',
        headers=headers,
    )
    assert gate.status_code == 200
    gate_data = gate.json()["data"]
    assert gate_data["already_published"] is True
    assert gate_data["published_version_id"] == first.json()["data"]["version_id"]
    assert gate_data["allowed"] is True
    assert gate_data["errors"] == []
    assert db.scalar(select(func.count(GraphVersion.id)).where(
        GraphVersion.build_run_id == build["build_run_id"]
    )) == 1
    assert db.get(GraphBuildRun, build["build_run_id"]).status == "published"


def test_patch_omission_clear_and_revision_conflict(client, db, auth_headers):
    headers = auth_headers()
    v1 = _publish_initial(client, db, headers)
    draft = client.post(
        "/api/v1/positions/BACKEND_ENGINEER/graph/drafts",
        json={"base_version_id": v1["version_id"]}, headers=headers,
    ).json()["data"]
    relation = db.scalar(select(PositionSkillRelationDraft).where(
        PositionSkillRelationDraft.build_run_id == draft["draft_id"]
    ))

    first = client.post(
        f"/api/v1/relations/{relation.id}/modify",
        json={
            "build_run_id": draft["draft_id"], "position_id": "BACKEND_ENGINEER",
            "expected_revision": 1, "weight": .9, "confidence": .95,
            "reason": "two manual overrides",
        }, headers=headers,
    )
    assert first.status_code == 200
    assert first.json()["data"]["revision"] == 2

    importance_only = client.post(
        f"/api/v1/relations/{relation.id}/modify",
        json={
            "build_run_id": draft["draft_id"], "position_id": "BACKEND_ENGINEER",
            "expected_revision": 2, "importance_level": "core",
            "reason": "PATCH one field",
        }, headers=headers,
    )
    assert importance_only.status_code == 200
    data = importance_only.json()["data"]
    assert data["revision"] == 3
    assert data["manual_weight"] == .9
    assert data["manual_confidence"] == .95
    assert data["manual_importance_level"] == "core"

    stale_edit = client.post(
        f"/api/v1/relations/{relation.id}/modify",
        json={
            "build_run_id": draft["draft_id"], "position_id": "BACKEND_ENGINEER",
            "expected_revision": 2, "weight": .1, "reason": "client B",
        }, headers=headers,
    )
    assert stale_edit.status_code == 409
    assert stale_edit.json()["details"] == {
        "current_revision": 3, "error_code": "RELATION_EDIT_CONFLICT"
    }
    db.expire_all()
    assert db.get(PositionSkillRelationDraft, relation.id).manual_weight == .9

    cleared = client.post(
        f"/api/v1/relations/{relation.id}/modify",
        json={
            "build_run_id": draft["draft_id"], "position_id": "BACKEND_ENGINEER",
            "expected_revision": 3, "weight": None, "reason": "clear weight",
        }, headers=headers,
    )
    assert cleared.status_code == 200
    assert cleared.json()["data"]["manual_weight"] is None
    assert cleared.json()["data"]["manual_confidence"] == .95
    assert cleared.json()["data"]["revision"] == 4


def test_position_build_relation_consistency_is_enforced(client, db, auth_headers):
    headers = auth_headers()
    v1 = _publish_initial(client, db, headers)
    draft_id = client.post(
        "/api/v1/positions/BACKEND_ENGINEER/graph/drafts",
        json={"base_version_id": v1["version_id"]}, headers=headers,
    ).json()["data"]["draft_id"]
    relation = db.scalar(select(PositionSkillRelationDraft).where(
        PositionSkillRelationDraft.build_run_id == draft_id
    ))
    wrong_build = client.post(
        f"/api/v1/relations/{relation.id}/modify",
        json={"build_run_id": draft_id + 999, "position_id": "BACKEND_ENGINEER",
              "expected_revision": 1, "weight": .8, "reason": "wrong build"},
        headers=headers,
    )
    assert wrong_build.status_code == 409
    wrong_position = client.post(
        f"/api/v1/relations/{relation.id}/modify",
        json={"build_run_id": draft_id, "position_id": "POS_OTHER",
              "expected_revision": 1, "weight": .8, "reason": "wrong position"},
        headers=headers,
    )
    assert wrong_position.status_code == 409

    db.add(StandardPosition(
        position_id="POS_OTHER", name="其他岗位", category_code="TECH"
    ))
    db.commit()
    relation.position_id = "POS_OTHER"
    with __import__("pytest").raises(IntegrityError):
        db.commit()
    db.rollback()


def test_historical_version_endpoint_and_provenance(client, db, auth_headers):
    headers = auth_headers()
    prepare_catalog(db)
    prepare_jd(client, headers)
    build = _new_build(client, headers)
    relation = db.scalar(select(PositionSkillRelationDraft).where(
        PositionSkillRelationDraft.build_run_id == build["build_run_id"]
    ))
    relation.auto_importance_level = "important"
    relation.manual_importance_level = "core"
    relation.final_importance_level = "core"
    db.commit()
    v1 = client.post(
        f'/api/v1/graph/build-runs/{build["build_run_id"]}/publish',
        json={}, headers=headers,
    ).json()["data"]

    draft_id = client.post(
        "/api/v1/positions/BACKEND_ENGINEER/graph/drafts",
        json={"base_version_id": v1["version_id"]}, headers=headers,
    ).json()["data"]["draft_id"]
    draft_graph = client.get(
        f"/api/v1/graph/build-runs/{draft_id}/graph", headers=headers
    ).json()["data"]
    inherited = draft_graph["skill_relations"][0]
    assert inherited["auto_importance_level"] == "important"
    assert inherited["manual_importance_level"] == "core"
    assert inherited["final_importance_level"] == "core"

    approve_build_tasks(client, draft_id, headers)
    v2 = client.post(
        f"/api/v1/graph/build-runs/{draft_id}/publish", json={}, headers=headers
    ).json()["data"]
    current_before = db.scalar(select(StandardPosition.current_version_id).where(
        StandardPosition.position_id == "BACKEND_ENGINEER"
    ))
    for expected, number in ((v1, 1), (v2, 2)):
        response = client.get(
            f'/api/v1/positions/BACKEND_ENGINEER/graph/versions/{expected["version_id"]}'
        )
        assert response.status_code == 200
        detail = response.json()["data"]
        assert detail["version_number"] == number
        assert detail["source_version"]
        assert detail["snapshot"]["skill_relations"][0]["auto_importance_level"] == "important"
    assert client.get(
        f'/api/v1/positions/POS_OTHER/graph/versions/{v1["version_id"]}'
    ).status_code == 404
    assert db.scalar(select(StandardPosition.current_version_id).where(
        StandardPosition.position_id == "BACKEND_ENGINEER"
    )) == current_before


def test_diff_is_stably_sorted(client, db, auth_headers):
    headers = auth_headers()
    v1 = _publish_initial(client, db, headers)
    build = _new_build(client, headers)
    v2 = client.post(
        f'/api/v1/graph/build-runs/{build["build_run_id"]}/publish',
        json={}, headers=headers,
    ).json()["data"]
    path = "/api/v1/positions/BACKEND_ENGINEER/graph/versions/diff"
    params = {"from_version_id": v1["version_id"], "to_version_id": v2["version_id"]}
    first = client.get(path, params=params).json()["data"]
    second = client.get(path, params=params).json()["data"]
    assert first == second
    for key in ("added", "removed", "changed"):
        assert [item["skill_id"] for item in first[key]] == sorted(
            item["skill_id"] for item in first[key]
        )


def test_standard_graph_is_global_catalog_but_lifecycle_is_system_role_only(
    client, db, auth_headers
):
    admin = auth_headers()
    enterprise = auth_headers("enterprise_user")
    v1 = _publish_initial(client, db, admin)
    draft = client.post(
        "/api/v1/positions/BACKEND_ENGINEER/graph/drafts",
        json={"base_version_id": v1["version_id"]}, headers=admin,
    ).json()["data"]
    relation = db.scalar(select(PositionSkillRelationDraft).where(
        PositionSkillRelationDraft.build_run_id == draft["draft_id"]
    ))
    assert client.post(
        "/api/v1/positions/BACKEND_ENGINEER/graph/drafts",
        json={"base_version_id": v1["version_id"]}, headers=enterprise,
    ).status_code == 403
    assert client.post(
        f"/api/v1/relations/{relation.id}/modify",
        json={"build_run_id": draft["draft_id"], "position_id": "BACKEND_ENGINEER",
              "expected_revision": 1, "weight": .8, "reason": "forbidden"},
        headers=enterprise,
    ).status_code == 403
    assert client.post(
        f'/api/v1/graph/build-runs/{draft["draft_id"]}/publish',
        json={}, headers=enterprise,
    ).status_code == 403
    # Published standard-position snapshots are global reference data, not
    # enterprise-owned objects, so safe reads do not cross a tenant boundary.
    assert client.get(
        f'/api/v1/positions/BACKEND_ENGINEER/graph/versions/{v1["version_id"]}'
    ).status_code == 200


def _concurrent_app(tmp_path):
    app = create_app(Settings(
        database_url=f"sqlite:///{(tmp_path / 'concurrent.db').as_posix()}",
        jwt_secret_key="strict-concurrency-test-secret-32-bytes",
        build_jobs_inline=True,
    ))
    Base.metadata.create_all(app.state.database.engine)
    with app.state.database.session_factory() as session:
        session.add_all([
            User(username="admin", password_hash=hash_password("secret"), role="admin"),
            PositionCategory(code="TECH", name="技术"),
            SkillCategory(code="LANG", name="语言"),
            StandardPosition(position_id="BACKEND_ENGINEER", name="后端工程师", category_code="TECH"),
            Skill(skill_id="SKILL_PYTHON", canonical_name="Python", category_code="LANG"),
        ])
        session.commit()
        user = session.scalar(select(User).where(User.username == "admin"))
        token = create_token(user, app.state.settings)
    return app, {"Authorization": f"Bearer {token}"}


def test_concurrent_draft_creation_and_double_publish(tmp_path):
    app, headers = _concurrent_app(tmp_path)
    with TestClient(app) as setup:
        prepare_jd(setup, headers)
        first_build = setup.post(
            "/api/v1/positions/BACKEND_ENGINEER/graph/build", json={}, headers=headers
        ).json()["data"]
        approve_build_tasks(setup, first_build["build_run_id"], headers)
        v1 = setup.post(
            f'/api/v1/graph/build-runs/{first_build["build_run_id"]}/publish',
            json={}, headers=headers,
        ).json()["data"]

    def open_draft():
        with TestClient(app) as client:
            return client.post(
                "/api/v1/positions/BACKEND_ENGINEER/graph/drafts",
                json={"base_version_id": v1["version_id"]}, headers=headers,
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _index: open_draft(), range(2)))
    assert [response.status_code for response in responses] == [200, 200]
    draft_ids = {response.json()["data"]["draft_id"] for response in responses}
    assert len(draft_ids) == 1
    draft_id = draft_ids.pop()

    with TestClient(app) as setup:
        approve_build_tasks(setup, draft_id, headers)

    def publish():
        with TestClient(app) as client:
            return client.post(
                f"/api/v1/graph/build-runs/{draft_id}/publish",
                json={}, headers=headers,
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        published = list(pool.map(lambda _index: publish(), range(2)))
    assert all(response.status_code == 200 for response in published), [
        (response.status_code, response.json()) for response in published
    ]
    assert len({response.json()["data"]["version_id"] for response in published}) == 1
    with app.state.database.session_factory() as session:
        versions = session.scalars(select(GraphVersion).where(
            GraphVersion.build_run_id == draft_id
        )).all()
        assert len(versions) == 1
        position = session.scalar(select(StandardPosition).where(
            StandardPosition.position_id == "BACKEND_ENGINEER"
        ))
        assert position.current_version_id == versions[0].id
        assert session.scalar(select(func.count(GraphBuildRun.id)).where(
            GraphBuildRun.active_draft_key == f"BACKEND_ENGINEER:{v1['version_id']}"
        )) == 0
    app.state.database.engine.dispose()
