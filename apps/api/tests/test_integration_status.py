import pytest
from dataclasses import replace
from fastapi.testclient import TestClient

from app.contexts.cv_ingestion import CVIngestionUseCases
from app.contexts.data_validation import CVValidationPolicy, CVValidatorSet
from app.contexts.data_validation.fakes import FakeSkillCatalogResolutionPort
from app.infrastructure.cv_ingestion import (
    ApplicationResumeImporter,
    SqlAlchemyCVIngestionUnitOfWork,
)
from app.main import app
from app.models.jd import JobDescription
from app.models.source_cv import CVExtractionTask, SourceCV, SourceCVVersion
from tests.runtime_database import reset_database_data, Base, SessionLocal, engine
from tests.user_factory import create_internal_user


client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_database():
    reset_database_data()
    yield
    reset_database_data()


def _token(username: str, role: str) -> str:
    create_internal_user(username, role)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "password123"},
    )
    assert response.status_code == 200
    return response.json()["data"]["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_admin_portal_aggregates_jd_and_cv_status_without_orchestration():
    admin_token = _token("integration_status_admin", "admin")
    owner_id = create_internal_user("integration_status_cv_owner", "personal_user")
    assert owner_id is not None
    with SessionLocal() as session:
        jd = JobDescription(
            source_type="enterprise_upload",
            title="后端工程师",
            raw_text="要求 Python",
        )
        source = SourceCV(
            owner_id=owner_id,
            source_platform="personal_resume",
            source_record_id="portal-cv-1",
        )
        session.add_all([jd, source])
        session.flush()
        version = SourceCVVersion(
            source_cv_id=source.id,
            raw_text="熟练使用 Python",
            source_version="1",
        )
        session.add(version)
        session.flush()
        task = CVExtractionTask(
            source_cv_version_id=version.id,
            request_id="portal-test-request",
            status="failed",
            attempt_count=1,
            max_attempts=3,
            retryable=True,
        )
        session.add(task)
        session.commit()
        jd_id = jd.id
        task_id = task.id

    response = client.get(
        f"/api/v1/portal/admin/integration-status?jd_id={jd_id}&cv_task_id={task_id}",
        headers=_headers(admin_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    expected_stages = {
        "source", "extraction", "validation", "draft", "review", "publication",
        "outbox", "knowledge_graph", "discovery", "matching",
    }
    assert expected_stages <= data["jd"].keys()
    assert expected_stages <= data["cv"].keys()
    assert data["jd"]["draft"]["status"] == "not_started"
    assert data["cv"]["source"]["status"] == "versioned"
    assert data["cv"]["extraction"]["status"] == "failed"
    retry = next(
        action
        for action in data["cv"]["actions"]
        if action["code"] == "retry_cv_extraction"
    )
    assert retry == {
        "code": "retry_cv_extraction",
        "method": "POST",
        "endpoint": (
            f"/portal/admin/integration-status/cv-extraction-tasks/{task_id}/retry"
        ),
        "permission": "integration.cv.retry",
        "authorized": True,
        "enabled": True,
        "reason": None,
    }


def test_admin_portal_retry_queues_failed_owner_task_without_cv_read_access():
    admin_token = _token("integration_retry_admin", "admin")
    owner_id = create_internal_user("integration_retry_owner", "personal_user")
    assert owner_id is not None
    with SessionLocal() as session:
        source = SourceCV(
            owner_id=owner_id,
            source_platform="personal_resume",
            source_record_id="portal-retry-cv",
        )
        session.add(source)
        session.flush()
        version = SourceCVVersion(
            source_cv_id=source.id,
            raw_text="包含个人信息的完整简历",
            source_version="1",
        )
        session.add(version)
        session.flush()
        task = CVExtractionTask(
            source_cv_version_id=version.id,
            request_id="portal-retry-request",
            status="failed",
            attempt_count=1,
            max_attempts=3,
            retryable=True,
        )
        session.add(task)
        session.commit()
        task_id = task.id

    class _UnusedProvider:
        request_id = "unused-provider-request"

        def extract(self, *, document_id: str, raw_text: str, progress_callback=None):
            raise AssertionError("retry endpoint must not execute extraction")

    use_cases = CVIngestionUseCases(
        lambda: SqlAlchemyCVIngestionUnitOfWork(SessionLocal),
        _UnusedProvider(),
        ApplicationResumeImporter(app.state.container.resumes),
        CVValidatorSet(CVValidationPolicy(), FakeSkillCatalogResolutionPort),
        enabled=True,
        max_attempts=3,
    )
    original = app.state.container
    app.state.container = replace(original, cv_ingestion=use_cases)
    try:
        response = client.post(
            f"/api/v1/portal/admin/integration-status/cv-extraction-tasks/{task_id}/retry",
            headers=_headers(admin_token),
        )
        forbidden_read = client.get(
            f"/api/v1/cv-extraction-tasks/{task_id}",
            headers=_headers(admin_token),
        )
    finally:
        app.state.container = original

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "pending"
    assert forbidden_read.status_code == 403
    with SessionLocal() as session:
        queued = session.get(CVExtractionTask, task_id)
        assert queued.status == "pending"
        assert queued.retryable is False


@pytest.mark.parametrize(
    ("status", "attempt_count", "max_attempts", "retryable"),
    [
        ("running", 1, 3, False),
        ("succeeded", 1, 3, False),
        ("failed", 3, 3, False),
    ],
)
def test_portal_disables_non_retryable_cv_tasks(
    status: str, attempt_count: int, max_attempts: int, retryable: bool
):
    admin_token = _token(f"integration_disabled_{status}_{attempt_count}", "admin")
    owner_id = create_internal_user(
        f"integration_disabled_owner_{status}_{attempt_count}", "personal_user"
    )
    with SessionLocal() as session:
        source = SourceCV(
            owner_id=owner_id,
            source_platform="personal_resume",
            source_record_id=f"disabled-{status}-{attempt_count}",
        )
        session.add(source)
        session.flush()
        version = SourceCVVersion(
            source_cv_id=source.id,
            raw_text="测试",
            source_version="1",
        )
        session.add(version)
        session.flush()
        task = CVExtractionTask(
            source_cv_version_id=version.id,
            request_id=f"portal-{status}-{attempt_count}-request",
            status=status,
            attempt_count=attempt_count,
            max_attempts=max_attempts,
            retryable=retryable,
        )
        session.add(task)
        session.commit()
        task_id = task.id

    response = client.get(
        f"/api/v1/portal/admin/integration-status?cv_task_id={task_id}",
        headers=_headers(admin_token),
    )
    retry = next(
        action
        for action in response.json()["data"]["cv"]["actions"]
        if action["code"] == "retry_cv_extraction"
    )
    assert retry["enabled"] is False


def test_integration_status_requires_admin_permission_and_an_identifier():
    personal_token = _token("integration_status_personal", "personal_user")
    admin_token = _token("integration_status_admin_2", "admin")

    denied = client.get(
        "/api/v1/portal/admin/integration-status?jd_id=unknown",
        headers=_headers(personal_token),
    )
    invalid = client.get(
        "/api/v1/portal/admin/integration-status",
        headers=_headers(admin_token),
    )

    assert denied.status_code == 403
    assert invalid.status_code == 422


def test_reviewer_portal_exposes_cv_write_actions_as_disabled():
    reviewer_token = _token("integration_status_reviewer", "reviewer")
    owner_id = create_internal_user(
        "integration_status_reviewer_owner",
        "personal_user",
    )
    with SessionLocal() as session:
        source = SourceCV(
            owner_id=owner_id,
            source_platform="personal_resume",
            source_record_id="reviewer-visible-cv",
        )
        session.add(source)
        session.flush()
        version = SourceCVVersion(
            source_cv_id=source.id,
            raw_text="Python",
            source_version="1",
        )
        session.add(version)
        session.flush()
        task = CVExtractionTask(
            source_cv_version_id=version.id,
            request_id="reviewer-visible-request",
            status="failed",
            attempt_count=1,
            max_attempts=3,
            retryable=True,
        )
        session.add(task)
        session.commit()
        task_id = task.id

    response = client.get(
        f"/api/v1/portal/admin/integration-status?cv_task_id={task_id}",
        headers=_headers(reviewer_token),
    )

    assert response.status_code == 200
    actions = {
        action["code"]: action for action in response.json()["data"]["cv"]["actions"]
    }
    expected_permissions = {
        "retry_cv_extraction": "integration.cv.retry",
        "confirm_cv_parse_result": "resume.parse.manage",
        "generate_resume_skill_profile": "resume.profile.generate",
        "create_match": "matching.run",
        "create_learning_path": "learning_path.create",
    }
    assert {
        code: action["permission"] for code, action in actions.items()
    } == expected_permissions
    assert all(action["enabled"] is False for action in actions.values())
    assert all(action["authorized"] is False for action in actions.values())
    assert all(
        action["reason"] == f"Missing permission: {action['permission']}"
        for action in actions.values()
    )
