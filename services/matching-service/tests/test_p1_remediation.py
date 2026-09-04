from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient

from app.bootstrap.application import create_app
from app.domain.auth import AuthContext, derive_access_scope
from app.infrastructure.authentication import (
    FakeAuthenticationProvider,
    JwtOidcAuthenticationProvider,
)
from app.infrastructure.redis_task_queue import RedisTaskQueue
from app.infrastructure.resource_authorization import (
    InMemoryApplicationGrantAdapter,
    InMemoryCVAuthorizationAdapter,
)
from app.infrastructure.sqlalchemy_repositories import SQLAlchemyPersistence


def _context(subject: str, tenant: str, role: str) -> AuthContext:
    roles = frozenset({role})
    return AuthContext(
        subject_id=subject,
        tenant_id=tenant,
        roles=roles,
        access_scope=derive_access_scope(subject, tenant, roles),
        token_id=f"jti-{subject}",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


def _headers(token: str, context: AuthContext, idem: str | None = None) -> dict[str, str]:
    result = {
        "Authorization": f"Bearer {token}",
        "X-Access-Scope": context.access_scope,
    }
    if idem:
        result["Idempotency-Key"] = idem
    return result


def test_all_business_endpoints_reject_anonymous_access():
    client = TestClient(create_app(auth_env={}, runtime_env={"MATCHING_RUNTIME_MODE": "test"}))
    requests = (
        client.post("/api/v1/profiles/cv/validate", json={}),
        client.post("/api/v1/profiles/position/validate", json={}),
        client.post("/api/v1/evaluations", json={}),
        client.post("/api/v1/learning-paths", json={}),
        client.post("/api/v1/integrations/evaluate", json={}),
        client.post("/api/v1/evaluation-tasks", json={}),
        client.get("/api/v1/evaluation-tasks/unknown"),
        client.get("/api/v1/evaluations/unknown"),
    )
    assert [response.status_code for response in requests] == [401] * len(requests)
    assert client.get("/health/ready").status_code == 200


def test_candidate_cannot_submit_another_users_trusted_cv(
    ready_cv_json, ready_position_json
):
    alice = _context("alice", "public", "candidate")
    app = create_app(
        authentication_provider=FakeAuthenticationProvider({"alice": alice}),
        cv_authorization=InMemoryCVAuthorizationAdapter(
            {("public", "alice", "different-owned-cv")}
        ),
    )
    response = TestClient(app).post(
        "/api/v1/evaluation-tasks",
        json={"cv_profile": ready_cv_json, "position_profile": ready_position_json},
        headers=_headers("alice", alice, "foreign-cv"),
    )
    assert response.status_code == 404
    assert response.json()["code"] == "RESOURCE_NOT_FOUND"


def test_synchronous_business_endpoint_rejects_forged_scope(
    ready_cv_json, ready_position_json
):
    alice = _context("alice", "public", "candidate")
    response = TestClient(
        create_app(authentication_provider=FakeAuthenticationProvider({"alice": alice}))
    ).post(
        "/api/v1/evaluations",
        json={"cv_profile": ready_cv_json, "position_profile": ready_position_json},
        headers={
            "Authorization": "Bearer alice",
            "X-Access-Scope": "user:forged:scope",
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == "ACCESS_SCOPE_MISMATCH"


def test_same_tenant_recruiter_without_application_grant_gets_not_found(
    ready_cv_json, ready_position_json
):
    owner = _context("recruiter-a", "enterprise-a", "recruiter")
    intruder = _context("recruiter-b", "enterprise-a", "recruiter")
    grant = (
        "enterprise-a",
        "recruiter-a",
        ready_cv_json["cv_id"],
        ready_position_json["position_id"],
    )
    app = create_app(
        authentication_provider=FakeAuthenticationProvider(
            {"owner": owner, "intruder": intruder}
        ),
        application_grants=InMemoryApplicationGrantAdapter({grant}),
    )
    client = TestClient(app)
    created = client.post(
        "/api/v1/evaluation-tasks",
        json={"cv_profile": ready_cv_json, "position_profile": ready_position_json},
        headers=_headers("owner", owner, "granted"),
    )
    task_id = created.json()["data"]["task"]["task_id"]
    denied = client.get(
        f"/api/v1/evaluation-tasks/{task_id}",
        headers=_headers("intruder", intruder),
    )
    assert denied.status_code == 404
    assert denied.json()["code"] == "RESOURCE_NOT_FOUND"


def test_production_rejects_memory_empty_and_fake_configuration():
    with pytest.raises(ValueError, match="production configuration is incomplete"):
        create_app(
            runtime_env={"MATCHING_RUNTIME_MODE": "production"},
            persistence_env={"MATCHING_PERSISTENCE_PROVIDER": "memory"},
            queue_env={"MATCHING_QUEUE_PROVIDER": "memory"},
            auth_env={"MATCHING_AUTH_MODE": "fake"},
        )


def test_complete_production_configuration_selects_formal_adapters():
    runtime = {
        "MATCHING_RUNTIME_MODE": "production",
        "MATCHING_CV_SOURCE_URL": "https://cv.invalid",
        "MATCHING_POSITION_SOURCE_URL": "https://position.invalid",
        "MATCHING_GRAPH_SOURCE_URL": "https://graph.invalid",
        "MATCHING_GRAPH_VERSION": "graph.v1",
        "MATCHING_CV_AUTHORIZATION_URL": "https://auth.invalid/cv",
        "MATCHING_APPLICATION_GRANT_URL": "https://auth.invalid/application",
        "MATCHING_UPSTREAM_SERVICE_TOKEN": "opaque-service-credential",
    }
    redis_queue = RedisTaskQueue(
        object(), queue_name="matching:test", visibility_timeout_seconds=30
    )
    app = create_app(
        runtime_env=runtime,
        persistence_env={
            "MATCHING_PERSISTENCE_PROVIDER": "postgres",
            "MATCHING_DATABASE_URL": "postgresql+psycopg://u:p@database.invalid/matching",
        },
        queue_env={
            "MATCHING_QUEUE_PROVIDER": "redis",
            "MATCHING_REDIS_URL": "redis://redis.invalid:6379/0",
        },
        auth_env={
            "MATCHING_AUTH_MODE": "jwt",
            "MATCHING_AUTH_ISSUER": "https://issuer.invalid",
            "MATCHING_AUTH_AUDIENCE": "matching-api",
            "MATCHING_AUTH_VERIFICATION_KEY": "verification-key-at-least-32-bytes",
        },
        task_queue=redis_queue,
    )
    assert app.state.runtime_mode == "production"
    assert isinstance(app.state.persistence, SQLAlchemyPersistence)
    assert isinstance(app.state.task_queue, RedisTaskQueue)
    assert app.state.persistence_provider == "postgres"
    assert app.state.queue_provider == "redis"
    app.state.persistence.dispose()


def test_non_array_jwt_roles_returns_stable_claim_error():
    key = "correct-key-for-hs256-at-least-32-bytes"
    token = jwt.encode(
        {
            "sub": "u",
            "tenant_id": "t",
            "roles": 7,
            "jti": "j",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            "iss": "https://issuer.example",
            "aud": "matching-api",
        },
        key,
        algorithm="HS256",
    )
    provider = JwtOidcAuthenticationProvider(
        issuer="https://issuer.example",
        audience="matching-api",
        verification_key=key,
    )
    response = TestClient(create_app(authentication_provider=provider)).get(
        "/api/v1/evaluation-tasks/x",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "TOKEN_CLAIMS_INVALID"
