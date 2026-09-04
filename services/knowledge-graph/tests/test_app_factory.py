from fastapi.testclient import TestClient
import jwt
import pytest
from sqlalchemy import text

from app.auth import create_token_for, hash_password
from app.config import Settings
from app.database import Base
from app.main import create_app
from app.models import User


def test_create_app_instances_have_isolated_engines(tmp_path):
    first = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'first.db'}"))
    second = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'second.db'}"))
    assert first.state.database.engine is not second.state.database.engine
    assert str(first.state.database.engine.url) != str(second.state.database.engine.url)
    first.state.database.engine.dispose()
    second.state.database.engine.dispose()


def test_readiness_uses_factory_owned_database(tmp_path):
    application = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'ready.db'}"))
    with TestClient(application) as client:
        response = client.get("/readiness")
    assert response.status_code == 200
    assert response.json()["data"]["database"] == "ok"
    application.state.database.engine.dispose()


CUSTOM_SECRET = "custom-jwt-secret-with-at-least-32-characters"
OTHER_SECRET = "other-custom-secret-with-at-least-32-characters"


def _auth_app(tmp_path, name: str, secret: str):
    application = create_app(Settings(
        database_url=f"sqlite:///{tmp_path / name}", jwt_secret_key=secret
    ))
    Base.metadata.create_all(application.state.database.engine)
    with application.state.database.session_factory() as session:
        user = User(username="admin", password_hash=hash_password("secret"), role="admin")
        session.add(user)
        session.commit()
        session.refresh(user)
        user_id = user.id
    return application, user_id


def test_custom_jwt_secret_signs_and_verifies():
    runtime = Settings(jwt_secret_key=CUSTOM_SECRET)
    token = create_token_for(7, "admin", runtime)
    assert jwt.decode(token, CUSTOM_SECRET, algorithms=["HS256"])["sub"] == "7"


def test_token_signed_with_default_secret_is_rejected_when_custom_secret_configured(tmp_path):
    application, user_id = _auth_app(tmp_path, "custom.db", CUSTOM_SECRET)
    forged = create_token_for(user_id, "admin", Settings())
    with TestClient(application) as client:
        response = client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {forged}"}
        )
    assert response.status_code == 401


def test_token_signed_with_historical_public_secret_is_rejected(tmp_path):
    application, user_id = _auth_app(tmp_path, "historical-public.db", CUSTOM_SECRET)
    forged = create_token_for(
        user_id,
        "admin",
        Settings(jwt_secret_key="change-this-knowledge-graph-jwt-secret"),
    )
    with TestClient(application) as client:
        response = client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {forged}"}
        )
    assert response.status_code == 401


def test_token_signed_with_custom_secret_accesses_auth_me(tmp_path):
    application, user_id = _auth_app(tmp_path, "valid-token.db", CUSTOM_SECRET)
    token = create_token_for(user_id, "admin", application.state.settings)
    with TestClient(application) as client:
        response = client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 200
    assert response.json()["data"]["role"] == "admin"


def test_two_app_instances_with_same_secret_accept_each_other_tokens(tmp_path):
    first, user_id = _auth_app(tmp_path, "first-same-secret.db", CUSTOM_SECRET)
    second, _ = _auth_app(tmp_path, "second-same-secret.db", CUSTOM_SECRET)
    token = create_token_for(user_id, "admin", first.state.settings)
    with TestClient(second) as client:
        response = client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 200


def test_token_signed_with_custom_secret_is_rejected_by_different_app_instance(tmp_path):
    first, user_id = _auth_app(tmp_path, "first-auth.db", CUSTOM_SECRET)
    second, _ = _auth_app(tmp_path, "second-auth.db", OTHER_SECRET)
    token = create_token_for(user_id, "admin", first.state.settings)
    with TestClient(second) as client:
        response = client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 401


def test_create_app_uses_injected_jwt_secret(tmp_path):
    application, _ = _auth_app(tmp_path, "injected.db", CUSTOM_SECRET)
    assert application.state.settings.jwt_secret_key == CUSTOM_SECRET
    assert application.state.providers["jwt_auth"]["status"] == "configured"


def test_production_rejects_missing_jwt_secret():
    with pytest.raises(ValueError, match="JWT_SECRET_KEY must be configured"):
        Settings(environment="production")


def test_production_rejects_short_jwt_secret():
    with pytest.raises(ValueError, match="at least 32"):
        Settings(environment="production", jwt_secret_key="too-short")


@pytest.mark.parametrize(
    "secret",
    [
        "",
        "change-this-knowledge-graph-jwt-secret",
        "change-this-in-production-change-me",
        "development-only-change-me",
        "change-me",
    ],
)
def test_production_rejects_empty_placeholder_and_historical_jwt_secrets(secret):
    with pytest.raises(ValueError):
        Settings(environment="production", jwt_secret_key=secret)


def test_production_requires_strong_service_password_and_valid_config_starts(tmp_path):
    jwt_secret = "valid-kg-secret-with-at-least-32-characters"
    with pytest.raises(ValueError, match="must be configured"):
        Settings(environment="production", jwt_secret_key=jwt_secret)
    with pytest.raises(ValueError, match="KNOWLEDGE_GRAPH_SERVICE_PASSWORD"):
        Settings(
            environment="production",
            jwt_secret_key=jwt_secret,
            service_password="change-me",
        )
    with pytest.raises(ValueError, match="requires PostgreSQL"):
        Settings(
            catalog_writes_enabled=False,
            database_url=f"sqlite:///{tmp_path / 'production-start.db'}",
            environment="production",
            jwt_secret_key=jwt_secret,
            service_password="a-unique-kg-service-password",
        )
    configured = Settings(
        catalog_writes_enabled=False,
        database_url="postgresql+psycopg://kg:secret@postgres/knowledge_graph",
        environment="production",
        jwt_secret_key=jwt_secret,
        service_password="a-unique-kg-service-password",
    )
    assert configured.database_url.startswith("postgresql+psycopg://")

    with pytest.raises(ValueError, match="BUILD_JOBS_INLINE"):
        Settings(
            catalog_writes_enabled=False,
            database_url="postgresql+psycopg://kg:secret@postgres/knowledge_graph",
            environment="production",
            jwt_secret_key=jwt_secret,
            service_password="a-unique-kg-service-password",
            build_jobs_inline=True,
        )


@pytest.mark.parametrize("legacy_name", ["KNOWLEDGE_GRAPH_PASSWORD", "KG_SERVICE_PASSWORD"])
def test_production_from_env_rejects_service_password_alias(monkeypatch, legacy_name):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", "valid-kg-secret-with-at-least-32-characters")
    monkeypatch.setenv("KNOWLEDGE_GRAPH_SERVICE_PASSWORD", "a-unique-kg-service-password")
    monkeypatch.setenv(legacy_name, "legacy-alias-cannot-bypass-validation")
    with pytest.raises(ValueError, match="Unsupported knowledge graph password"):
        Settings.from_env()


@pytest.mark.parametrize(
    "legacy_name",
    ["JWT_SECRET", "KNOWLEDGE_GRAPH_JWT_SECRET", "SECRET_KEY"],
)
def test_production_from_env_rejects_legacy_jwt_secret_names(monkeypatch, legacy_name):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv(
        "JWT_SECRET_KEY", "valid-kg-secret-with-at-least-32-characters"
    )
    monkeypatch.setenv(legacy_name, "legacy-secret-with-at-least-32-characters")
    with pytest.raises(ValueError, match="Unsupported JWT secret"):
        Settings.from_env()


def test_production_from_env_does_not_accept_legacy_jwt_secret_without_formal_key(
    monkeypatch,
):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.setenv("JWT_SECRET", "legacy-secret-with-at-least-32-characters")
    with pytest.raises(ValueError, match="Unsupported JWT secret"):
        Settings.from_env()
