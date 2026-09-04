from __future__ import annotations

import httpx
import hashlib
import pytest
from dataclasses import replace
from fastapi.testclient import TestClient
from jose import jwt

from app.contexts.matching_learning.matching_service import (
    MatchingIdentity,
    MatchingServiceError,
    RemoteEvaluation,
)
from app.contexts.matching_learning.ports import ResumeProfile
from app.domain.accounts import AccountActor
from app.domain.errors import PermissionDenied
from app.infrastructure.matching_service import (
    HttpMatchingServiceAdapter,
    SqlAlchemyMatchingIdentityAdapter,
)
from app.main import app
from app.models.candidate_submission import CandidateSubmission
from app.models.enterprise import Enterprise
from app.models.enterprise_job import EnterpriseJob
from app.models.matching_service_reference import MatchingServiceReference
from app.models.learning_path_record import LearningPathRecord
from app.models.resume import Resume
from app.models.user import User
from tests.runtime_database import reset_database_data, Base, SessionLocal, engine


client = TestClient(app)


class _LearningPathService:
    def __init__(
        self,
        *,
        stale: bool = False,
        existing_routes: bool = False,
        reject_generation: bool = False,
    ) -> None:
        self.stale = stale
        self.existing_routes = existing_routes
        self.reject_generation = reject_generation
        self.requested_budgets: list[float | None] = []

    def get_evaluation(self, identity, evaluation_id, *, correlation_id):
        return RemoteEvaluation(
            evaluation_id=evaluation_id,
            task_id=f"task-{evaluation_id}",
            stale=self.stale,
            evaluation={"evaluation_id": evaluation_id},
            gap_analysis={
                "generation_status": "completed",
                "learning_routes": ([{"route_type": "fastest_employment"}] if self.existing_routes else []),
                "prioritized_gaps": [{"requirement_id": "requirement-1"}],
                "learning_path": [],
            },
            versions={"position_graph_version": "graph-v1"},
            created_at="2026-08-13T00:00:00Z",
            updated_at="2026-08-13T00:00:00Z",
            provider="matching-service",
            algorithm_versions={"gap": "gap-v1"},
            data_versions={"graph": "graph-v1"},
        )

    def generate_learning_path(self, *args, **kwargs):
        budget = kwargs.get("time_budget_hours")
        self.requested_budgets.append(budget)
        if self.reject_generation:
            return {
                "generation_status": "rejected",
                "error_code": "LEARNING_PATH_PROFILE_INVALID",
                "error_message": "both profile contracts are required",
                "prioritized_gaps": [],
                "learning_routes": [],
                "learning_path": [],
            }
        return {
            "generation_status": "completed",
            "learning_routes": [],
            "prioritized_gaps": [{"requirement_id": "requirement-1"}],
            "learning_path": [{"step_order": 1, "objective": "Close the gap"}],
            "time_budget_hours": budget,
        }


class _LearningPathResumes:
    def get(self, resume_id):
        return ResumeProfile(resume_id, "owner", "snapshot-v1", (), ())


class _LearningPathContracts:
    def __init__(self, *, cv_version="cv-v1", position_version="position-v1"):
        self.cv_version = cv_version
        self.position_version = position_version

    def cv_profile(self, cv_id, snapshot_id=None):
        return {"source_version": self.cv_version}

    def position_profile(self, position_id):
        return {"source_version": self.position_version}


def _matching_reference(
    user_id: str, evaluation_id: str, resume_id: str
) -> MatchingServiceReference:
    return MatchingServiceReference(
        task_id=f"task-{evaluation_id}",
        evaluation_id=evaluation_id,
        user_id=user_id,
        tenant_id=f"personal:{user_id}",
        resume_id=resume_id,
        position_id="position-1",
        provider="matching-service",
        status="current",
        idempotency_key=f"idem-{evaluation_id}",
        access_scope=f"user:{user_id}",
        source_version="cv=cv-v1|position=position-v1",
        cv_profile_version="cv-v1",
        position_profile_version="position-v1",
        taxonomy_version="taxonomy-v1",
        graph_version="graph-v1",
        algorithm_version="matching-v1",
    )


def test_learning_path_create_list_and_detail_preserve_owner_evaluation_and_budget():
    owner_id, owner_token = _register("learning-path-owner")
    other_id, other_token = _register("learning-path-other")
    with SessionLocal() as session:
        owner_resume = Resume(user_id=owner_id, source_type="text", raw_text="owner")
        other_resume = Resume(user_id=other_id, source_type="text", raw_text="other")
        session.add_all([owner_resume, other_resume])
        session.flush()
        session.add_all([
            _matching_reference(owner_id, "evaluation-owner", owner_resume.id),
            _matching_reference(other_id, "evaluation-other", other_resume.id),
        ])
        session.commit()

    original = app.state.container
    service = _LearningPathService()
    app.state.container = replace(
        original,
        learning_paths=replace(
            original.learning_paths,
            service=service,
            resumes=_LearningPathResumes(),
            contracts=_LearningPathContracts(),
        ),
    )
    try:
        first = client.post(
            "/api/v1/learning-paths",
                json={
                    "evaluation_id": "evaluation-owner",
                    "target_position_id": "position-1",
                    "time_budget_hours": 12,
            },
            headers=_headers(owner_token),
        )
        second = client.post(
            "/api/v1/learning-paths",
                json={
                    "evaluation_id": "evaluation-owner",
                    "target_position_id": "position-1",
                    "time_budget_hours": 24,
            },
            headers=_headers(owner_token),
        )
        other = client.post(
            "/api/v1/learning-paths",
            json={"evaluation_id": "evaluation-other", "time_budget_hours": 8},
            headers=_headers(other_token),
        )
        assert first.status_code == second.status_code == other.status_code == 200
        first_data = first.json()["data"]
        second_data = second.json()["data"]
        assert first_data["path_id"] != second_data["path_id"]
        assert {
            first_data["time_budget_hours"], second_data["time_budget_hours"]
        } == {12, 24}
        assert first_data["evaluation_id"] == "evaluation-owner"
        assert first_data["target_position_id"] == "position-1"
        assert first_data["algorithm_versions"]["gap"] == "gap-v1"
        assert first_data["data_versions"]["graph"] == "graph-v1"
        assert service.requested_budgets == [12, 24, 8]

        owner_list = client.get("/api/v1/learning-paths", headers=_headers(owner_token))
        assert owner_list.status_code == 200
        owner_items = owner_list.json()["data"]
        assert {item["path_id"] for item in owner_items} == {
            first_data["path_id"], second_data["path_id"]
        }
        assert {item["evaluation_id"] for item in owner_items} == {"evaluation-owner"}
        other_items = client.get(
            "/api/v1/learning-paths", headers=_headers(other_token)
        ).json()["data"]
        assert len(other_items) == 1
        assert other_items[0]["evaluation_id"] == "evaluation-other"

        detail = client.get(
            f"/api/v1/learning-paths/{first_data['path_id']}",
            headers=_headers(owner_token),
        )
        assert detail.status_code == 200
        assert detail.json()["data"] == first_data
        assert client.get(
            f"/api/v1/learning-paths/{first_data['path_id']}",
            headers=_headers(other_token),
        ).status_code == 404
    finally:
        app.state.container = original

    with SessionLocal() as session:
        rows = session.query(LearningPathRecord).filter(
            LearningPathRecord.evaluation_id == "evaluation-owner"
        ).all()
        assert len(rows) == 2
        assert {row.time_budget_hours for row in rows} == {12, 24}
        assert {row.user_id for row in rows} == {owner_id}


def test_learning_path_regenerates_when_embedded_routes_exist_but_steps_were_deferred():
    owner_id, token = _register("learning-path-deferred")
    with SessionLocal() as session:
        resume = Resume(user_id=owner_id, source_type="text", raw_text="owner")
        session.add(resume)
        session.flush()
        session.add(_matching_reference(owner_id, "evaluation-deferred", resume.id))
        session.commit()

    original = app.state.container
    service = _LearningPathService(existing_routes=True)
    app.state.container = replace(
        original,
        learning_paths=replace(
            original.learning_paths,
            service=service,
            resumes=_LearningPathResumes(),
            contracts=_LearningPathContracts(),
        ),
    )
    try:
        response = client.post(
            "/api/v1/learning-paths",
            json={"evaluation_id": "evaluation-deferred"},
            headers=_headers(token),
        )
        assert response.status_code == 200
        assert response.json()["data"]["stages"][0]["objective"] == "Close the gap"
        assert service.requested_budgets == [None]
    finally:
        app.state.container = original


def test_learning_path_rejects_target_different_from_source_evaluation():
    owner_id, token = _register("learning-path-target-mismatch")
    with SessionLocal() as session:
        resume = Resume(user_id=owner_id, source_type="text", raw_text="owner")
        session.add(resume)
        session.flush()
        session.add(_matching_reference(owner_id, "evaluation-target", resume.id))
        session.commit()

    original = app.state.container
    service = _LearningPathService()
    app.state.container = replace(
        original,
        learning_paths=replace(
            original.learning_paths,
            service=service,
            resumes=_LearningPathResumes(),
            contracts=_LearningPathContracts(),
        ),
    )
    try:
        response = client.post(
            "/api/v1/learning-paths",
            json={
                "evaluation_id": "evaluation-target",
                "target_position_id": "another-position",
            },
            headers=_headers(token),
        )
        assert response.status_code == 409
        assert service.requested_budgets == []
    finally:
        app.state.container = original

    with SessionLocal() as session:
        assert session.query(LearningPathRecord).count() == 0


def test_rejected_learning_path_is_not_persisted_as_empty_history():
    owner_id, token = _register("learning-path-rejected")
    with SessionLocal() as session:
        resume = Resume(user_id=owner_id, source_type="text", raw_text="owner")
        session.add(resume)
        session.flush()
        session.add(_matching_reference(owner_id, "evaluation-rejected", resume.id))
        session.commit()

    original = app.state.container
    service = _LearningPathService(reject_generation=True)
    app.state.container = replace(
        original,
        learning_paths=replace(
            original.learning_paths,
            service=service,
            resumes=_LearningPathResumes(),
            contracts=_LearningPathContracts(),
        ),
    )
    try:
        response = client.post(
            "/api/v1/learning-paths",
            json={"evaluation_id": "evaluation-rejected", "time_budget_hours": 40},
            headers=_headers(token),
        )
        assert response.status_code == 409
        data = response.json()["data"]
        assert data["error_code"] == "LEARNING_PATH_PROFILE_INVALID"
        assert data["message"] == "both profile contracts are required"
    finally:
        app.state.container = original

    with SessionLocal() as session:
        assert session.query(LearningPathRecord).count() == 0


def test_learning_path_rejects_missing_or_stale_evaluation_without_persisting():
    owner_id, token = _register("learning-path-stale")
    with SessionLocal() as session:
        resume = Resume(user_id=owner_id, source_type="text", raw_text="stale")
        session.add(resume)
        session.flush()
        session.add(_matching_reference(owner_id, "evaluation-stale", resume.id))
        session.commit()

    original = app.state.container
    app.state.container = replace(
        original,
        learning_paths=replace(
            original.learning_paths,
            service=_LearningPathService(stale=True),
            resumes=_LearningPathResumes(),
            contracts=_LearningPathContracts(),
        ),
    )
    try:
        missing = client.post(
            "/api/v1/learning-paths",
            json={"evaluation_id": "evaluation-missing"},
            headers=_headers(token),
        )
        stale = client.post(
            "/api/v1/learning-paths",
            json={"evaluation_id": "evaluation-stale"},
            headers=_headers(token),
        )
        assert missing.status_code == 404
        assert stale.status_code == 409
    finally:
        app.state.container = original

    with SessionLocal() as session:
        assert session.query(LearningPathRecord).count() == 0


def test_learning_path_rejects_locally_stale_evaluation_before_generation():
    owner_id, token = _register("learning-path-version-drift")
    with SessionLocal() as session:
        resume = Resume(user_id=owner_id, source_type="text", raw_text="drift")
        session.add(resume)
        session.flush()
        session.add(_matching_reference(owner_id, "evaluation-drift", resume.id))
        session.commit()

    original = app.state.container
    service = _LearningPathService(stale=False)
    app.state.container = replace(
        original,
        learning_paths=replace(
            original.learning_paths,
            service=service,
            resumes=_LearningPathResumes(),
            contracts=_LearningPathContracts(cv_version="cv-v2"),
        ),
    )
    try:
        response = client.post(
            "/api/v1/learning-paths",
            json={"evaluation_id": "evaluation-drift", "time_budget_hours": 16},
            headers=_headers(token),
        )
        assert response.status_code == 409
        assert service.requested_budgets == []
    finally:
        app.state.container = original

    with SessionLocal() as session:
        assert session.query(LearningPathRecord).count() == 0


@pytest.fixture(autouse=True)
def reset_database():
    reset_database_data()
    yield
    reset_database_data()


def _register(username: str, role: str = "personal_user") -> tuple[str, str]:
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "password123", "role": role},
    )
    if response.status_code not in {200, 400}:
        pytest.fail(response.text)
    token = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "password123"},
    ).json()["data"]["access_token"]
    with SessionLocal() as session:
        user_id = session.query(User).filter(User.username == username).one().id
    return user_id, token


def _headers(token: str, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **extra}


def _response(status: str, *, evaluation_id: str | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        request=httpx.Request("POST", "http://matching/api/v1/evaluation-tasks"),
        json={
            "code": 0,
            "message": "success",
            "data": {
                "created": True,
                "task": {
                    "task_id": "task-1",
                    "status": status,
                    "evaluation_id": evaluation_id,
                    "attempt": 0,
                    "created_at": "2026-07-27T00:00:00Z",
                    "updated_at": "2026-07-27T00:00:00Z",
                    "error_code": None,
                    "error_message": None,
                },
            },
        },
    )


@pytest.mark.parametrize("status", ["pending", "running", "succeeded", "failed"])
def test_http_matching_adapter_propagates_identity_idempotency_and_status(monkeypatch, status):
    captured: dict[str, object] = {}

    def request(method, url, **kwargs):
        captured.update({"method": method, "url": url, **kwargs})
        return _response(status, evaluation_id="evaluation-1" if status == "succeeded" else None)

    monkeypatch.setattr(httpx, "request", request)
    adapter = HttpMatchingServiceAdapter(
        base_url="http://matching",
        issuer="jobgraph-main",
        audience="matching-service",
        signing_key="x" * 40,
        timeout_seconds=1,
        max_retries=0,
        retry_backoff_seconds=0,
    )
    identity = MatchingIdentity("subject-1", "tenant-1", ("candidate",), "user:scope",)
    result = adapter.create_task(
        identity,
        cv_id="cv-1",
        position_id="position-1",
        idempotency_key="idem-1",
        correlation_id="request-1",
    )

    assert result.status == status
    assert captured["json"] == {
        "schema_version": "matching-evaluation-request.v1",
        "tenant_id": "tenant-1",
        "target_type": "standard_position",
        "cv_id": "cv-1",
            "position_id": "position-1",
            "use_enterprise_weights": False,
            "generate_learning_path": False,
        }
    headers = captured["headers"]
    assert headers["Idempotency-Key"] == "idem-1"
    assert headers["X-Access-Scope"] == "user:scope"
    assert headers["X-Request-ID"] == headers["X-Correlation-ID"] == "request-1"
    claims = jwt.decode(
        headers["Authorization"].removeprefix("Bearer "),
        "x" * 40,
        algorithms=["HS256"],
        audience="matching-service",
        issuer="jobgraph-main",
    )
    assert claims["sub"] == "subject-1"
    assert claims["tenant_id"] == "tenant-1"
    assert claims["roles"] == ["candidate"]


@pytest.mark.parametrize(
    ("upstream_status", "expected_code"),
    [(429, "MATCHING_SERVICE_RATE_LIMITED"), (503, "MATCHING_SERVICE_UNAVAILABLE")],
)
def test_http_matching_adapter_maps_stable_dependency_errors(
    monkeypatch, upstream_status, expected_code
):
    monkeypatch.setattr(
        httpx,
        "request",
        lambda *args, **kwargs: httpx.Response(
            upstream_status, request=httpx.Request("POST", "http://matching")
        ),
    )
    adapter = HttpMatchingServiceAdapter(
        base_url="http://matching",
        issuer="issuer",
        audience="audience",
        signing_key="x" * 40,
        timeout_seconds=1,
        max_retries=0,
        retry_backoff_seconds=0,
    )
    with pytest.raises(MatchingServiceError) as raised:
        adapter.create_task(
            MatchingIdentity("subject", "tenant", ("candidate",), "scope"),
            cv_id="cv",
            position_id="position",
            idempotency_key="idem",
            correlation_id="request",
        )
    assert raised.value.code == expected_code
    assert raised.value.status_code == 503


def test_http_matching_adapter_maps_timeout_without_fabricating_success(monkeypatch):
    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("probe timeout")

    monkeypatch.setattr(httpx, "request", timeout)
    adapter = HttpMatchingServiceAdapter(
        base_url="https://matching.invalid",
        issuer="jobgraph-main",
        audience="matching-service",
        signing_key="k" * 40,
        timeout_seconds=0.01,
        max_retries=0,
        retry_backoff_seconds=0,
    )
    with pytest.raises(MatchingServiceError) as raised:
        adapter.create_task(
            MatchingIdentity("subject", "tenant", ("candidate",), "scope"),
            cv_id="cv",
            position_id="position",
            idempotency_key="idem",
            correlation_id="correlation",
        )
    assert raised.value.code == "MATCHING_SERVICE_TIMEOUT"
    assert raised.value.status_code == 503


def test_authorization_uses_cv_owner_and_exact_application_grant():
    candidate_id, _ = _register("candidate-owner")
    other_id, _ = _register("candidate-other")
    recruiter_id, _ = _register("recruiter", role="enterprise_user")
    with SessionLocal() as session:
        resume = Resume(
            user_id=candidate_id,
            source_type="text",
            raw_text="redacted",
            parse_status="completed",
        )
        enterprise = Enterprise(
            owner_user_id=recruiter_id,
            enterprise_name="Contract Test Enterprise",
            status="active",
        )
        session.add_all([resume, enterprise])
        session.flush()
        job = EnterpriseJob(
            enterprise_id=enterprise.id,
            title="Backend Engineer",
            standard_position_id="position-authorized",
            status="published",
        )
        session.add(job)
        session.flush()
        session.add(
            CandidateSubmission(
                resume_id=resume.id,
                enterprise_job_id=job.id,
                enterprise_id=enterprise.id,
                resume_owner_user_id=candidate_id,
                status="submitted",
            )
        )
        enterprise_job_id = job.id
        session.commit()
        resume_id = resume.id

    identities = SqlAlchemyMatchingIdentityAdapter(SessionLocal)
    identities.authorize_request(
        AccountActor(candidate_id, "personal_user"),
        cv_id=resume_id,
        position_id="any-position",
    )
    identities.authorize_request(
        AccountActor(candidate_id, "personal_user"),
        cv_id=resume_id,
        position_id=enterprise_job_id,
        target_type="enterprise_job",
    )
    with pytest.raises(PermissionDenied):
        identities.authorize_request(
            AccountActor(other_id, "personal_user"),
            cv_id=resume_id,
            position_id="any-position",
        )
    identities.authorize_request(
        AccountActor(recruiter_id, "enterprise_user"),
        cv_id=resume_id,
        position_id="position-authorized",
    )
    with pytest.raises(PermissionDenied):
        identities.authorize_request(
            AccountActor(recruiter_id, "enterprise_user"),
            cv_id=resume_id,
            position_id="position-not-authorized",
        )


def test_missing_validated_snapshot_is_rejected_without_writing_local_rules():
    user_id, token = _register("matching_facade_disabled")
    with SessionLocal() as session:
        resume = Resume(user_id=user_id, source_type="text", raw_text="Python")
        session.add(resume)
        session.commit()
        resume_id = resume.id
        before = session.query(MatchingServiceReference).count()

    response = client.post(
        "/api/v1/matches/tasks",
        json={
            "resume_id": resume_id,
            "target_type": "standard_position",
            "target_id": "missing-position",
        },
        headers=_headers(token, **{"Idempotency-Key": "idem-disabled"}),
    )
    assert response.status_code == 400
    assert "CV_SNAPSHOT_UNAVAILABLE" in response.text
    with SessionLocal() as session:
        assert session.query(MatchingServiceReference).count() == before

    preflight = client.get(
        "/api/v1/matches/preflight",
        params={"resume_id": resume_id, "position_id": "missing-position"},
        headers=_headers(token),
    )
    assert preflight.status_code == 200
    gate = preflight.json()["data"]
    assert gate["ready"] is False
    assert gate["cv_snapshot_ready"] is False
    assert gate["position_profile_ready"] is False
    assert gate["blockers"] == [
        "CV_SNAPSHOT_UNAVAILABLE",
        "POSITION_PROFILE_UNAVAILABLE",
    ]


def test_contract_service_credential_and_cv_owner_authorization():
    user_id, _ = _register("matching_contract_owner")
    with SessionLocal() as session:
        resume = Resume(user_id=user_id, source_type="text", raw_text="contract")
        session.add(resume)
        session.commit()
        resume_id = resume.id

    original = app.extra["runtime_settings"].MATCHING_UPSTREAM_SERVICE_TOKEN
    app.extra["runtime_settings"].MATCHING_UPSTREAM_SERVICE_TOKEN = "s" * 40
    try:
        assert client.post(
            "/api/v1/authorization/cv-owner",
            json={"subject_id": user_id, "tenant_id": "ignored", "cv_id": resume_id},
        ).status_code == 401
        allowed = client.post(
            "/api/v1/authorization/cv-owner",
            json={"subject_id": user_id, "tenant_id": "ignored", "cv_id": resume_id},
            headers={"Authorization": f"Bearer {'s' * 40}"},
        )
        assert allowed.status_code == 200
        assert allowed.json() == {"data": {"authorized": True}}
        denied = client.post(
            "/api/v1/authorization/cv-owner",
            json={"subject_id": "other-user", "tenant_id": "ignored", "cv_id": resume_id},
            headers={"Authorization": f"Bearer {'s' * 40}"},
        )
        assert denied.status_code == 404
    finally:
        app.extra["runtime_settings"].MATCHING_UPSTREAM_SERVICE_TOKEN = original
