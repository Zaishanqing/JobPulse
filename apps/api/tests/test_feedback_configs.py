import pytest
from fastapi.testclient import TestClient

from tests.runtime_database import SessionLocal, reset_database_data
from app.main import app
from app.models.enterprise import Enterprise
from app.models.feedback import FeedbackRecord
from app.models.jd import JobDescription
from app.models.resume import Resume
from app.models.user import User
from app.models.system_config import SystemConfig
from tests.user_factory import create_internal_user


client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_database():
    reset_database_data()
    yield
    reset_database_data()


def _headers(username: str, role: str) -> dict:
    create_internal_user(username, role)
    client.post(
        "/api/v1/auth/register",
        json={
            "role": role,
            "username": username,
            "password": "password123",
            "email": f"{username}@example.com",
            "phone": "13800000000",
        },
    )
    token = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "password123"},
    ).json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _user_id(username: str) -> str:
    with SessionLocal() as session:
        return session.query(User.id).filter(User.username == username).scalar()


def _resume(owner_username: str, *, resume_id: str) -> None:
    with SessionLocal() as session:
        session.add(
            Resume(
                id=resume_id,
                user_id=_user_id(owner_username),
                source_type="text",
                raw_text="Python",
            )
        )
        session.commit()


def _jd(owner_username: str, *, jd_id: str) -> None:
    with SessionLocal() as session:
        enterprise = Enterprise(
            owner_user_id=_user_id(owner_username), enterprise_name=f"org-{jd_id}"
        )
        session.add(enterprise)
        session.flush()
        session.add(
            JobDescription(
                id=jd_id,
                enterprise_id=enterprise.id,
                source_type="enterprise_upload",
                title="Engineer",
                raw_text="Java",
            )
        )
        session.commit()


def test_feedback_type_role_matrix_and_persisted_owner_edit():
    personal = _headers("feedback_personal", "personal_user")
    enterprise = _headers("feedback_enterprise", "enterprise_user")
    _resume("feedback_personal", resume_id="resume-1")
    _jd("feedback_enterprise", jd_id="jd-1")

    personal_feedback = client.post(
        "/api/v1/feedback/resume-parse",
        headers=personal,
        json={"resume_id": "resume-1", "correction": "Python"},
    )
    wrong_personal = client.post(
        "/api/v1/feedback/resume-parse", headers=enterprise, json={"correction": "x"}
    )
    enterprise_feedback = client.post(
        "/api/v1/feedback/jd-parse",
        headers=enterprise,
        json={"jd_id": "jd-1", "correction": "Java"},
    )
    wrong_enterprise = client.post(
        "/api/v1/feedback/jd-parse", headers=personal, json={"correction": "x"}
    )

    assert personal_feedback.status_code == 200
    assert enterprise_feedback.status_code == 200
    assert wrong_personal.status_code == 403
    assert wrong_enterprise.status_code == 403
    feedback_id = personal_feedback.json()["data"]["feedback_id"]
    assert personal_feedback.json()["data"]["implementation_status"] == "database_persisted_review_queue"

    edited = client.put(
        f"/api/v1/feedback/{feedback_id}",
        headers=personal,
        json={"payload": {"resume_id": "resume-1", "correction": "Python 3"}},
    )
    assert edited.status_code == 200
    assert edited.json()["data"]["payload"]["correction"] == "Python 3"
    assert len(client.get("/api/v1/feedback", headers=personal).json()["data"]) == 1
    assert len(client.get("/api/v1/feedback", headers=enterprise).json()["data"]) == 1


def test_feedback_cross_user_access_and_review_status_are_enforced():
    owner = _headers("feedback_owner", "personal_user")
    other = _headers("feedback_other", "personal_user")
    admin = _headers("feedback_admin", "admin")
    _resume("feedback_owner", resume_id="resume-review")
    feedback_id = client.post(
        "/api/v1/feedback/resume-parse",
        headers=owner,
        json={"resume_id": "resume-review", "rating": 2},
    ).json()["data"]["feedback_id"]

    assert client.get(f"/api/v1/feedback/{feedback_id}", headers=other).status_code == 403
    assert client.put(
        f"/api/v1/feedback/{feedback_id}", headers=owner, json={"status": "accepted"}
    ).status_code == 403

    reviewed = client.put(
        f"/api/v1/feedback/{feedback_id}", headers=admin, json={"status": "accepted"}
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["data"]["status"] == "accepted"
    assert reviewed.json()["data"]["payload"]["review_audit"]["operator_id"] == _user_id(
        "feedback_admin"
    )
    assert reviewed.json()["data"]["payload"]["review_audit"]["result"] == "accepted"
    assert len(client.get("/api/v1/feedback", headers=admin).json()["data"]) == 1
    locked_edit = client.put(
        f"/api/v1/feedback/{feedback_id}", headers=owner, json={"payload": {"rating": 5}}
    )
    assert locked_edit.status_code == 409


def test_feedback_target_existence_ownership_and_duplicate_are_enforced():
    owner = _headers("feedback_target_owner", "personal_user")
    other = _headers("feedback_target_other", "personal_user")
    _resume("feedback_target_owner", resume_id="owned-resume")

    missing = client.post(
        "/api/v1/feedback/resume-parse",
        headers=owner,
        json={"object_type": "resume", "resume_id": "missing"},
    )
    forbidden = client.post(
        "/api/v1/feedback/resume-parse",
        headers=other,
        json={"object_type": "resume", "resume_id": "owned-resume"},
    )
    created = client.post(
        "/api/v1/feedback/resume-parse",
        headers=owner,
        json={"object_type": "resume", "resume_id": "owned-resume"},
    )
    duplicate = client.post(
        "/api/v1/feedback/resume-parse",
        headers=owner,
        json={"object_id": "owned-resume"},
    )
    invalid_type = client.post(
        "/api/v1/feedback/resume-parse",
        headers=owner,
        json={"object_type": "jd", "resume_id": "owned-resume"},
    )

    assert missing.status_code == 404
    assert forbidden.status_code == 403
    assert created.status_code == 200
    assert duplicate.status_code == 409
    assert invalid_type.status_code == 422


def test_feedback_status_machine_rejects_rollback_and_repeated_processing():
    owner = _headers("feedback_state_owner", "personal_user")
    reviewer = _headers("feedback_state_reviewer", "reviewer")
    _resume("feedback_state_owner", resume_id="state-resume")
    feedback_id = client.post(
        "/api/v1/feedback/resume-parse",
        headers=owner,
        json={"resume_id": "state-resume"},
    ).json()["data"]["feedback_id"]

    assert client.put(
        f"/api/v1/feedback/{feedback_id}",
        headers=reviewer,
        json={"status": "reviewing"},
    ).status_code == 200
    assert client.put(
        f"/api/v1/feedback/{feedback_id}",
        headers=reviewer,
        json={"status": "pending_review"},
    ).status_code == 409
    assert client.put(
        f"/api/v1/feedback/{feedback_id}",
        headers=reviewer,
        json={"status": "rejected"},
    ).status_code == 200
    assert client.put(
        f"/api/v1/feedback/{feedback_id}",
        headers=reviewer,
        json={"status": "rejected"},
    ).status_code == 409


def test_feedback_list_has_stable_pagination_count_and_filters():
    owner = _headers("feedback_page_owner", "personal_user")
    admin = _headers("feedback_page_admin", "admin")
    owner_id = _user_id("feedback_page_owner")
    for index in range(5):
        resume_id = f"page-resume-{index}"
        _resume("feedback_page_owner", resume_id=resume_id)
        assert client.post(
            "/api/v1/feedback/resume-parse",
            headers=owner,
            json={"resume_id": resume_id},
        ).status_code == 200
    with SessionLocal() as session:
        session.add(
            FeedbackRecord(
                feedback_type="match_report",
                created_by=owner_id,
                payload={
                    "object_type": "matching_evaluation",
                    "object_id": "evaluation-page",
                },
                status="accepted",
            )
        )
        session.commit()

    first = client.get("/api/v1/feedback?page=1&page_size=3", headers=admin)
    second = client.get("/api/v1/feedback?page=2&page_size=3", headers=admin)
    first_ids = {item["feedback_id"] for item in first.json()["data"]}
    second_ids = {item["feedback_id"] for item in second.json()["data"]}

    assert first.headers["X-Total-Count"] == "6"
    assert second.headers["X-Total-Count"] == "6"
    assert len(first_ids) == len(second_ids) == 3
    assert first_ids.isdisjoint(second_ids)
    pending = client.get(
        "/api/v1/feedback?status=pending_review&page_size=10", headers=admin
    )
    match_reports = client.get(
        "/api/v1/feedback?feedback_type=match_report", headers=admin
    )
    assert pending.headers["X-Total-Count"] == "5"
    assert {item["status"] for item in pending.json()["data"]} == {"pending_review"}
    assert match_reports.headers["X-Total-Count"] == "1"
    assert match_reports.json()["data"][0]["feedback_type"] == "match_report"


def test_system_config_is_persisted_versioned_and_shared_with_score_config():
    admin = _headers("config_admin", "admin")
    initial = client.get("/api/v1/system/config/germination-score", headers=admin)
    assert initial.status_code == 200
    assert initial.json()["data"]["version"] == 1

    updated = client.put(
        "/api/v1/system/config/germination-score",
        headers=admin,
        json={"growth": 0.45},
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["version"] == 2
    assert updated.json()["data"]["config"]["growth"] == 0.45
    assert updated.json()["data"]["implementation_status"] == "database_persisted_configuration"

    score_config = client.get("/api/v1/emerging-positions/score-config", headers=admin)
    assert score_config.status_code == 200
    assert score_config.json()["data"]["growth"] == 0.45

    new_client = TestClient(app)
    persisted = new_client.get("/api/v1/system/config/germination-score", headers=admin)
    assert persisted.json()["data"]["version"] == 2
    assert persisted.json()["data"]["config"]["growth"] == 0.45


def test_system_config_permissions_unknown_names_and_secret_fields():
    admin = _headers("config_admin_two", "admin")
    personal = _headers("config_personal", "personal_user")

    assert client.get("/api/v1/system/config/algorithms", headers=personal).status_code == 403
    assert client.get("/api/v1/system/config/not-found", headers=admin).status_code == 404
    rejected = client.put(
        "/api/v1/system/config/llm",
        headers=admin,
        json={"api_key": "must-not-be-stored"},
    )
    assert rejected.status_code == 400
    current = client.get("/api/v1/system/config/llm", headers=admin).json()["data"]
    assert "api_key" not in current["config"]
    assert "must-not-be-stored" not in str(current)


def test_model_service_config_encrypts_key_and_never_returns_plaintext(monkeypatch):
    admin = _headers("model_config_admin", "admin")
    saved = client.put(
        "/api/v1/system/model-service-config",
        headers=admin,
        json={
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "api_key": "sk-private-model-key",
        },
    )
    assert saved.status_code == 200
    assert saved.json()["data"]["api_key_configured"] is True
    assert "sk-private-model-key" not in saved.text
    assert app.state.container.system_configs.resolve_runtime_model_service() == (
        "https://api.deepseek.com",
        "deepseek-chat",
        "sk-private-model-key",
    )

    with SessionLocal() as session:
        stored = session.get(SystemConfig, "llm")
        assert stored is not None
        assert stored.config["api_key_ciphertext"] != "sk-private-model-key"

    generic = client.get("/api/v1/system/config/llm", headers=admin)
    assert "api_key_ciphertext" not in generic.json()["data"]["config"]

    class AvailableResponse:
        def raise_for_status(self):
            return None

    calls = []

    def post_model(*args, **kwargs):
        calls.append((args, kwargs))
        return AvailableResponse()

    monkeypatch.setattr("app.api.v1.system.httpx.post", post_model)
    tested = client.post(
        "/api/v1/system/model-service-config/test",
        headers=admin,
        json={"base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
    )
    assert tested.status_code == 200
    assert tested.json()["data"]["status"] == "available"
    assert calls[0][0][0] == "https://api.deepseek.com/chat/completions"
    assert calls[0][1]["json"]["model"] == "deepseek-chat"
