import pytest
from fastapi.testclient import TestClient

from tests.runtime_database import reset_database_data, Base, SessionLocal, engine
from app.main import app
from app.models.task_record import TaskRecord
from app.models.user import User
from app.services.task_service import create_task
from tests.user_factory import create_internal_user


client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_database():
    reset_database_data()
    yield
    reset_database_data()


def _register_and_login(username: str, role: str = "admin") -> tuple[str, str]:
    internal_user_id = create_internal_user(username, role)
    registered = client.post(
        "/api/v1/auth/register",
        json={
            "role": role,
            "username": username,
            "password": "password123",
        },
    )
    user_id = internal_user_id or registered.json()["data"]["user_id"]
    logged_in = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "password123"},
    )
    return user_id, logged_in.json()["data"]["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_jd(token: str) -> str:
    response = client.post(
        "/api/v1/jds/text",
        headers=_headers(token),
        json={"title": "Task Test JD", "raw_text": "Python FastAPI task test"},
    )
    assert response.status_code == 200
    return response.json()["data"]["jd_id"]


def _create_skill(token: str) -> str:
    response = client.post(
        "/api/v1/skills",
        headers=_headers(token),
        json={"skill_name": "Task Test Python", "description": "Python task test"},
    )
    assert response.status_code == 200
    return response.json()["data"]["skill_id"]


def test_completed_task_is_persisted_and_visible_across_requests():
    _, token = _register_and_login("task_admin001")
    jd_id = _create_jd(token)
    created = client.post(
        f"/api/v1/embeddings/jds/{jd_id}",
        headers=_headers(token),
    )
    task_id = created.json()["data"]["task_id"]

    detail = client.get(f"/api/v1/tasks/{task_id}", headers=_headers(token))
    listing = client.get("/api/v1/tasks", headers=_headers(token))
    logs = client.get(f"/api/v1/tasks/{task_id}/logs", headers=_headers(token))
    with SessionLocal() as db:
        persisted = db.query(TaskRecord).filter(TaskRecord.id == task_id).one()
        persisted_status = persisted.status

    assert created.status_code == 200
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "completed"
    assert detail.json()["data"]["canonical_status"] == "succeeded"
    assert detail.json()["data"]["implementation_status"] == "database_persisted_sync_executor"
    assert detail.json()["data"]["execution_mode"] == "synchronous_local"
    assert detail.json()["data"]["mock"] is False
    assert any(item["task_id"] == task_id for item in listing.json()["data"])
    assert [entry["status"] for entry in logs.json()["data"]["logs"]] == [
        "pending",
        "running",
        "succeeded",
    ]
    assert persisted_status == "succeeded"


def test_cancel_and_retry_enforce_persisted_state_transitions():
    user_id, token = _register_and_login("task_admin002")
    with SessionLocal() as db:
        user = db.query(User).filter(User.id == user_id).one()
        task_id = create_task(db, user, "manual_test").id

    cancelled = client.post(
        f"/api/v1/tasks/{task_id}/cancel",
        headers=_headers(token),
    )
    retried = client.post(
        f"/api/v1/tasks/{task_id}/retry",
        headers=_headers(token),
    )
    invalid_retry = client.post(
        f"/api/v1/tasks/{task_id}/retry",
        headers=_headers(token),
    )

    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["canonical_status"] == "cancelled"
    assert retried.status_code == 200
    assert retried.json()["data"]["canonical_status"] == "pending"
    assert retried.json()["data"]["attempt_count"] == 2
    assert invalid_retry.status_code == 409


def test_completed_and_missing_tasks_cannot_be_silently_cancelled():
    _, token = _register_and_login("task_admin003")
    skill_id = _create_skill(token)
    created = client.post(
        f"/api/v1/embeddings/skills/{skill_id}",
        headers=_headers(token),
    )
    task_id = created.json()["data"]["task_id"]

    completed_cancel = client.post(
        f"/api/v1/tasks/{task_id}/cancel",
        headers=_headers(token),
    )
    missing_cancel = client.post(
        "/api/v1/tasks/missing-task/cancel",
        headers=_headers(token),
    )

    assert completed_cancel.status_code == 409
    assert missing_cancel.status_code == 404


def test_general_task_registry_is_internal_only():
    _, personal_token = _register_and_login("task_personal004", "personal_user")

    assert client.get(
        "/api/v1/tasks",
        headers=_headers(personal_token),
    ).status_code == 403


def test_specialized_task_endpoints_reject_cross_type_ids_even_for_admin():
    user_id, token = _register_and_login("task_type_admin")
    with SessionLocal() as db:
        user = db.query(User).filter_by(id=user_id).one()
        task_ids = {
            task_type: create_task(db, user, task_type).id
            for task_type in (
                "resume_parse",
                "jd_parse",
                "match",
                "position_cluster",
                "trend_analysis",
                "predicted_position_analysis",
            )
        }

    wrong_cases = (
        ("/api/v1/matches/tasks/{}", task_ids["resume_parse"]),
        ("/api/v1/trend-analysis/tasks/{}", task_ids["jd_parse"]),
        ("/api/v1/position-clusters/tasks/{}", task_ids["match"]),
        (
            "/api/v1/predicted-positions/tasks/{}",
            task_ids["position_cluster"],
        ),
    )
    for path_template, task_id in wrong_cases:
        assert client.get(
            path_template.format(task_id), headers=_headers(token)
        ).status_code == 404

    correct_cases = (
        ("/api/v1/matches/tasks/{}", task_ids["match"]),
        ("/api/v1/trend-analysis/tasks/{}", task_ids["trend_analysis"]),
        ("/api/v1/position-clusters/tasks/{}", task_ids["position_cluster"]),
        (
            "/api/v1/predicted-positions/tasks/{}",
            task_ids["predicted_position_analysis"],
        ),
    )
    for path_template, task_id in correct_cases:
        response = client.get(path_template.format(task_id), headers=_headers(token))
        assert response.status_code == 200
        assert response.json()["data"]["task_id"] == task_id


def test_specialized_match_task_still_enforces_owner_access():
    owner_id, _ = _register_and_login("task_type_owner", "personal_user")
    _, other_token = _register_and_login("task_type_other", "personal_user")
    with SessionLocal() as db:
        owner = db.query(User).filter_by(id=owner_id).one()
        task_id = create_task(db, owner, "match").id

    assert client.get(
        f"/api/v1/matches/tasks/{task_id}", headers=_headers(other_token)
    ).status_code == 403
