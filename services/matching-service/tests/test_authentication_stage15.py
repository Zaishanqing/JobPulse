from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient

from app.application.authorization import AuthorizationError
from app.application.evaluation import MatchEvaluationService
from app.application.evaluation_tasks import EvaluationTaskService
from app.application.learning_paths import LearningPathService
from app.application.task_worker import EvaluationTaskWorker
from app.bootstrap.application import create_app
from app.domain.auth import AuthContext, derive_access_scope
from app.infrastructure.authentication import (
    FakeAuthenticationProvider,
    JwtOidcAuthenticationProvider,
    RejectingAuthenticationProvider,
    build_authentication_provider,
)
from app.infrastructure.memory_repositories import InMemoryPersistence
from app.infrastructure.memory_task_queue import InMemoryTaskQueue
from app.infrastructure.structured_logging import StructuredLogger

CORRECT_KEY = "correct-key-for-hs256-at-least-32-bytes"
WRONG_KEY = "wrong-key-for-hs256-at-least-32-bytes--"


def _context(subject: str, tenant: str, *roles: str, expired: bool = False) -> AuthContext:
    selected_roles = frozenset(roles)
    return AuthContext(
        subject_id=subject,
        tenant_id=tenant,
        roles=selected_roles,
        access_scope=derive_access_scope(subject, tenant, selected_roles),
        token_id=f"jti-{subject}",
        expires_at=datetime.now(timezone.utc)
        + (timedelta(seconds=-1) if expired else timedelta(hours=1)),
    )


def _payload(cv: dict, position: dict) -> dict:
    return {"cv_profile": cv, "position_profile": position}


def _headers(token: str, context: AuthContext, **extra: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Access-Scope": context.access_scope,
        **extra,
    }


def test_secure_default_rejects_anonymous_task_access(ready_cv_json, ready_position_json):
    application = create_app(auth_env={})
    response = TestClient(application).post(
        "/api/v1/evaluation-tasks",
        json=_payload(ready_cv_json, ready_position_json),
        headers={"Idempotency-Key": "anonymous"},
    )

    assert isinstance(application.state.authentication_provider, RejectingAuthenticationProvider)
    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_REQUIRED"
    assert isinstance(build_authentication_provider({}), RejectingAuthenticationProvider)


def test_forged_request_scope_is_rejected(ready_cv_json, ready_position_json):
    alice = _context("alice", "public", "candidate")
    app = create_app(authentication_provider=FakeAuthenticationProvider({"alice": alice}))

    response = TestClient(app).post(
        "/api/v1/evaluation-tasks",
        json=_payload(ready_cv_json, ready_position_json),
        headers={
            "Authorization": "Bearer alice",
            "X-Access-Scope": derive_access_scope("bob", "public", frozenset({"candidate"})),
            "Idempotency-Key": "forged-scope",
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "ACCESS_SCOPE_MISMATCH"


@pytest.mark.parametrize(
    ("owner", "intruder"),
    [
        (_context("alice", "public", "candidate"), _context("bob", "public", "candidate")),
        (
            _context("recruiter-a", "enterprise-a", "recruiter"),
            _context("recruiter-b", "enterprise-b", "recruiter"),
        ),
    ],
    ids=["cross-user", "cross-enterprise"],
)
def test_cross_domain_task_query_is_stable_not_found(
    owner, intruder, ready_cv_json, ready_position_json
):
    provider = FakeAuthenticationProvider({"owner": owner, "intruder": intruder})
    app = create_app(authentication_provider=provider)
    client = TestClient(app)
    created = client.post(
        "/api/v1/evaluation-tasks",
        json=_payload(ready_cv_json, ready_position_json),
        headers=_headers("owner", owner, **{"Idempotency-Key": "isolated"}),
    )
    task_id = created.json()["data"]["task"]["task_id"]

    denied = client.get(
        f"/api/v1/evaluation-tasks/{task_id}",
        headers=_headers("intruder", intruder),
    )

    assert denied.status_code == 404
    assert denied.json()["data"]["error_code"] == "TASK_NOT_FOUND"


def _jwt_token(
    key: str,
    *,
    audience: str = "matching-api",
    issuer: str = "https://issuer.example",
    expires_delta: timedelta = timedelta(minutes=5),
) -> str:
    return jwt.encode(
        {
            "sub": "jwt-user",
            "tenant_id": "jwt-tenant",
            "roles": ["candidate"],
            "jti": "jwt-token-id",
            "exp": datetime.now(timezone.utc) + expires_delta,
            "iss": issuer,
            "aud": audience,
        },
        key,
        algorithm="HS256",
    )


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        (_jwt_token(CORRECT_KEY, expires_delta=timedelta(seconds=-1)), "TOKEN_EXPIRED"),
        (_jwt_token(WRONG_KEY), "TOKEN_SIGNATURE_INVALID"),
        (_jwt_token(CORRECT_KEY, audience="other-api"), "TOKEN_AUDIENCE_INVALID"),
        (_jwt_token(CORRECT_KEY, issuer="https://wrong.example"), "TOKEN_ISSUER_INVALID"),
    ],
    ids=["expired", "bad-signature", "bad-audience", "bad-issuer"],
)
def test_jwt_validation_failures_return_stable_codes(token, expected):
    provider = JwtOidcAuthenticationProvider(
        issuer="https://issuer.example",
        audience="matching-api",
        verification_key=CORRECT_KEY,
    )
    app = create_app(authentication_provider=provider)

    response = TestClient(app).get(
        "/api/v1/evaluation-tasks/not-present",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == expected


def test_service_role_and_worker_role_are_separated(ready_cv_json, ready_position_json):
    service = _context("api-service", "platform", "matching.service")
    worker = _context("worker-1", "platform", "matching.worker")
    provider = FakeAuthenticationProvider({"service": service, "worker": worker})
    app = create_app(authentication_provider=provider)
    client = TestClient(app)

    allowed = client.post(
        "/api/v1/evaluation-tasks",
        json=_payload(ready_cv_json, ready_position_json),
        headers=_headers("service", service, **{"Idempotency-Key": "service-call"}),
    )
    denied = client.get(
        "/api/v1/evaluation-tasks/not-present", headers=_headers("worker", worker)
    )

    assert allowed.status_code == 200
    assert denied.status_code == 403
    assert denied.json()["code"] == "INSUFFICIENT_ROLE"

    storage = InMemoryPersistence()
    evaluation = MatchEvaluationService()
    tasks = EvaluationTaskService(
        storage.unit_of_work, evaluation, LearningPathService(evaluation)
    )
    EvaluationTaskWorker(
        InMemoryTaskQueue(), tasks, worker_id="worker-1", auth_context=worker
    )
    with pytest.raises(AuthorizationError, match="identity is not permitted"):
        EvaluationTaskWorker(
            InMemoryTaskQueue(), tasks, worker_id="bad-worker", auth_context=service
        )


def test_security_audit_excludes_tokens_and_pii(ready_cv_json, ready_position_json):
    stream = io.StringIO()
    logger = logging.getLogger("matching-stage15-security")
    logger.handlers = [logging.StreamHandler(stream)]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    context = _context("alice@example.com", "secret-company", "candidate")
    token = "top-secret-fake-token"
    app = create_app(
        authentication_provider=FakeAuthenticationProvider({token: context}),
        structured_logger=StructuredLogger(logger),
    )

    response = TestClient(app).post(
        "/api/v1/evaluation-tasks",
        json=_payload(ready_cv_json, ready_position_json),
        headers=_headers(token, context, **{"Idempotency-Key": "audit"}),
    )
    records = [json.loads(line) for line in stream.getvalue().splitlines()]

    assert response.status_code == 200
    assert any(record["event"] == "security_audit" for record in records)
    rendered = stream.getvalue()
    assert token not in rendered
    assert "alice@example.com" not in rendered
    assert "secret-company" not in rendered
