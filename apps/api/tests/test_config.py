from pathlib import Path
import os
import shutil
import subprocess

from jose import jwt
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
import yaml

from app.core.config import Settings, settings
from app.main import create_app
from app.models.user import User
from tests.runtime_database import SessionLocal, reset_database_data
from app.services.auth_service import hash_password

JOBPULSE_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = JOBPULSE_ROOT / "infra" / "compose" / "docker-compose.candidate.yml"
# Scripts and CI pin the project directory to infra; relative env_file paths in
# the compose file (e.g. ../config/semantic-demo-contract.env) resolve against
# it, so the test must pass the same flag instead of relying on the default
# (the compose file's parent directory).
COMPOSE_PROJECT_DIR = JOBPULSE_ROOT / "infra"


def test_pytest_uses_an_isolated_database():
    assert settings.ENVIRONMENT == "test"
    assert settings.DATABASE_URL.startswith("sqlite:///")
    database_path = Path(settings.DATABASE_URL.removeprefix("sqlite:///"))
    assert ".test-artifacts" in database_path.parts
    assert database_path.name == "test.db"
    assert "dev.db" not in settings.DATABASE_URL


def test_development_uses_non_predictable_process_local_jwt_secret():
    first = Settings(_env_file=None)
    second = Settings(_env_file=None)

    assert len(first.JWT_SECRET_KEY) >= 32
    assert first.JWT_SECRET_KEY != second.JWT_SECRET_KEY


def test_demo_admin_registration_is_explicitly_opt_in():
    assert Settings(_env_file=None).ALLOW_DEMO_ADMIN_REGISTRATION is False
    assert (
        Settings(
            ALLOW_DEMO_ADMIN_REGISTRATION="true", _env_file=None
        ).ALLOW_DEMO_ADMIN_REGISTRATION
        is True
    )


def test_production_requires_explicit_strong_jwt_secret():
    with pytest.raises(ValidationError, match="explicitly configured"):
        Settings(ENVIRONMENT="production", _env_file=None)

    with pytest.raises(ValidationError, match="at least 32 characters"):
        Settings(
            ENVIRONMENT="production",
            JWT_SECRET_KEY="too-short",
            _env_file=None,
        )

    configured = Settings(
        ENVIRONMENT="production",
        DATABASE_URL="postgresql+psycopg://jobgraph_main:secret@postgres/jobgraph_main",
        JWT_SECRET_KEY="a-unique-production-secret-with-32-plus-characters",
        KNOWLEDGE_GRAPH_SERVICE_PASSWORD="a-unique-kg-service-password",
        MATCHING_SERVICE_ENABLED=True,
        MATCHING_SERVICE_BASE_URL="https://matching.internal",
        MATCHING_SERVICE_SIGNING_KEY="a-unique-matching-signing-key-with-32-chars",
        MATCHING_UPSTREAM_SERVICE_TOKEN="a-unique-upstream-token-with-more-than-32-chars",
        _env_file=None,
    )
    assert configured.ENVIRONMENT == "production"


def test_development_rejects_sqlite_runtime_database():
    with pytest.raises(ValidationError, match="must use PostgreSQL"):
        Settings(
            ENVIRONMENT="development",
            DATABASE_URL="sqlite:///./data/dev.db",
            _env_file=None,
        )


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
    with pytest.raises(ValidationError):
        Settings(ENVIRONMENT="production", JWT_SECRET_KEY=secret, _env_file=None)


def test_production_rejects_missing_or_weak_knowledge_graph_password(monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_GRAPH_SERVICE_PASSWORD", raising=False)
    monkeypatch.delenv("KNOWLEDGE_GRAPH_PASSWORD", raising=False)
    monkeypatch.delenv("KG_SERVICE_PASSWORD", raising=False)
    values = {
        "ENVIRONMENT": "production",
        "DATABASE_URL": "postgresql+psycopg://jobgraph_main:secret@postgres/jobgraph_main",
        "JWT_SECRET_KEY": "a-unique-production-secret-with-32-plus-characters",
        "EMERGING_DISCOVERY_INTERNAL_TOKEN": "a-unique-discovery-token-with-32-plus-characters",
        "MATCHING_SERVICE_ENABLED": True,
        "MATCHING_SERVICE_BASE_URL": "https://matching.internal",
        "MATCHING_SERVICE_SIGNING_KEY": "a-unique-matching-signing-key-with-32-chars",
        "MATCHING_UPSTREAM_SERVICE_TOKEN": "a-unique-upstream-token-with-more-than-32-chars",
        "_env_file": None,
    }
    with pytest.raises(ValidationError, match="explicitly configured"):
        Settings(**values)
    with pytest.raises(ValidationError, match="KNOWLEDGE_GRAPH_SERVICE_PASSWORD"):
        Settings(**values, KNOWLEDGE_GRAPH_SERVICE_PASSWORD="change-me")
    configured = Settings(
        **values,
        KNOWLEDGE_GRAPH_SERVICE_PASSWORD="a-unique-kg-service-password",
    )
    assert configured.KNOWLEDGE_GRAPH_SERVICE_PASSWORD == "a-unique-kg-service-password"


@pytest.mark.parametrize("legacy_name", ["KNOWLEDGE_GRAPH_PASSWORD", "KG_SERVICE_PASSWORD"])
def test_production_rejects_knowledge_graph_password_aliases(monkeypatch, legacy_name):
    monkeypatch.setenv(legacy_name, "alias-cannot-bypass-formal-password-validation")
    with pytest.raises(ValidationError, match="Unsupported knowledge graph password"):
        Settings(
            ENVIRONMENT="production",
            JWT_SECRET_KEY="a-unique-production-secret-with-32-plus-characters",
            EMERGING_DISCOVERY_INTERNAL_TOKEN="a-unique-discovery-token-with-32-plus-characters",
            KNOWLEDGE_GRAPH_SERVICE_PASSWORD="a-unique-kg-service-password",
            _env_file=None,
        )


@pytest.mark.parametrize(
    "legacy_name",
    ["JWT_SECRET", "KNOWLEDGE_GRAPH_JWT_SECRET", "SECRET_KEY"],
)
def test_production_rejects_legacy_jwt_secret_environment_names(
    monkeypatch, legacy_name
):
    monkeypatch.setenv(legacy_name, "legacy-secret-with-at-least-32-characters")
    with pytest.raises(ValidationError, match="Unsupported JWT secret"):
        Settings(
            ENVIRONMENT="production",
            JWT_SECRET_KEY="valid-main-secret-with-at-least-32-characters",
            _env_file=None,
        )


def test_historical_public_jwt_secret_cannot_forge_main_auth_me():
    reset_database_data()
    with SessionLocal() as db:
        db.add(
            User(
                username="forgery-target",
                hashed_password=hash_password("password123"),
                role="admin",
            )
        )
        db.commit()

    forged = jwt.encode(
        {"sub": "forgery-target"},
        "change-this-knowledge-graph-jwt-secret",
        algorithm=settings.JWT_ALGORITHM,
    )
    with TestClient(create_app()) as client:
        response = client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {forged}"}
        )
    assert response.status_code == 401


def test_unknown_external_provider_names_fail_configuration_validation():
    with pytest.raises(ValidationError):
        Settings(LLM_PROVIDER="pretend-real-provider", _env_file=None)
    with pytest.raises(ValidationError):
        Settings(VECTOR_STORE_PROVIDER="pretend-vector-db", _env_file=None)


def test_validation_controls_use_safe_defaults(monkeypatch):
    for name in (
        "DATA_VALIDATION_MODE",
        "CV_EXTRACTION_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)

    configured = Settings(_env_file=None)

    assert configured.DATA_VALIDATION_MODE == "off"
    assert configured.CV_EXTRACTION_ENABLED is False


@pytest.mark.parametrize("mode", ["off", "observe", "enforce"])
def test_data_validation_mode_accepts_only_declared_values(mode):
    configured = Settings(DATA_VALIDATION_MODE=mode, _env_file=None)

    assert configured.DATA_VALIDATION_MODE == mode


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (False, False),
        ("true", True),
        ("false", False),
        ("TRUE", True),
        ("FALSE", False),
        ("TrUe", True),
        ("FaLsE", False),
    ],
)
def test_validation_boolean_controls_accept_only_declared_values(value, expected):
    name = "CV_EXTRACTION_ENABLED"
    values = {name: value}
    if expected:
        values.update(
            CV_EXTRACTION_BASE_URL="http://cv-extraction:8000",
            CV_EXTRACTION_INTERNAL_TOKEN="cv-internal-token-with-at-least-32-characters",
        )
    configured = Settings(**values, _env_file=None)

    assert getattr(configured, name) is expected
    assert isinstance(getattr(configured, name), bool)


@pytest.mark.parametrize(
    "value",
    [
        "yes",
        "no",
        "on",
        "off",
        "1",
        "0",
        1,
        0,
        "",
        " ",
        "\t",
        " true",
        "false ",
        "not-a-boolean",
    ],
)
def test_validation_boolean_controls_reject_undeclared_values(value):
    name = "CV_EXTRACTION_ENABLED"
    with pytest.raises(ValidationError):
        Settings(**{name: value}, _env_file=None)


@pytest.mark.parametrize(
    "value",
    ["", " ", "\t", "OFF", "Observe", "ENFORCE", " off", "observe ", "invalid"],
)
def test_data_validation_mode_rejects_empty_case_whitespace_and_unknown_values(value):
    with pytest.raises(ValidationError):
        Settings(DATA_VALIDATION_MODE=value, _env_file=None)


@pytest.mark.parametrize(
    "value",
    [0, -1, "0", "-1", "", " ", "\t", "invalid"],
)
def test_data_validation_worker_poll_seconds_must_be_positive(value):
    with pytest.raises(ValidationError):
        Settings(DATA_VALIDATION_WORKER_POLL_SECONDS=value, _env_file=None)


def test_extraction_token_has_no_weak_default_and_rejects_weak_values(monkeypatch):
    monkeypatch.delenv("JD_EXTRACTION_INTERNAL_TOKEN", raising=False)
    defaults = Settings(_env_file=None)
    assert defaults.JD_EXTRACTION_INTERNAL_TOKEN is None
    assert defaults.JD_EXTRACTION_WORKER_ENABLED is False
    with pytest.raises(ValidationError, match="JD_EXTRACTION_INTERNAL_TOKEN"):
        Settings(JD_EXTRACTION_INTERNAL_TOKEN="change-me", _env_file=None)
    with pytest.raises(ValidationError, match="lease timeout"):
        Settings(
            JD_EXTRACTION_WORKER_POLL_INTERVAL_SECONDS=10,
            JD_EXTRACTION_WORKER_LEASE_TIMEOUT_SECONDS=5,
            _env_file=None,
        )


def test_docker_compose_config_has_no_public_jwt_default_and_declares_runtime_modes():
    compose_source = COMPOSE_FILE.read_text(encoding="utf-8")
    assert "change-this-knowledge-graph-jwt-secret" not in compose_source
    assert "change-this-in-production-change-me" not in compose_source
    compose = yaml.safe_load(compose_source)
    services = compose["services"]
    for service_name in (
        "main-backend",
        "extraction-worker",
        "validation-worker",
        "kg-outbox-worker",
    ):
        assert services[service_name]["environment"]["ENVIRONMENT"] == (
            "${ENVIRONMENT:-production}"
        )
    assert services["extraction-worker"]["command"] == ["jobgraph-extraction-worker"]
    assert services["validation-worker"]["command"] == ["jobgraph-validation-worker"]
    assert services["kg-outbox-worker"]["command"] == ["jobgraph-outbox-worker"]
    assert services["extraction-worker"]["environment"]["JD_EXTRACTION_WORKER_ENABLED"] is True
    assert services["validation-worker"]["environment"]["DATA_VALIDATION_MODE"] == (
        "${DATA_VALIDATION_MODE:-enforce}"
    )
    for service_name in ("knowledge-graph-backend", "emerging-discovery"):
        assert services[service_name]["environment"]["ENVIRONMENT"] == "production"
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is unavailable; source-level compose assertions passed")
    env = os.environ.copy()
    env.update(
        {
            "MAIN_BACKEND_JWT_SECRET_KEY": "main-compose-secret-with-at-least-32-characters",
            "KNOWLEDGE_GRAPH_JWT_SECRET_KEY": "kg-compose-secret-with-at-least-32-characters",
            "KNOWLEDGE_GRAPH_SERVICE_PASSWORD": "compose-kg-service-password-strong",
            "EMERGING_DISCOVERY_INTERNAL_TOKEN": "compose-discovery-token-with-32-characters",
            "TREND_INTELLIGENCE_INTERNAL_TOKEN": "compose-trend-token-with-at-least-32-characters",
            "JD_EXTRACTION_INTERNAL_TOKEN": "compose-extraction-token-with-32-characters",
            "CV_EXTRACTION_INTERNAL_TOKEN": "compose-cv-token-with-at-least-32-characters",
            "CV_EXTRACTION_TAXONOMY_VERSION": "skill-taxonomy-snapshot.v1",
            "MATCHING_SERVICE_SIGNING_KEY": "compose-matching-signing-key-with-32-characters",
            "MATCHING_UPSTREAM_SERVICE_TOKEN": "compose-matching-upstream-token-with-32-characters",
            "MAIN_POSTGRES_PASSWORD": "compose-main-postgres-password",
            "CRAWLER_CORS_ALLOWED_ORIGINS": "http://localhost:3000",
            "DEEPSEEK_API_KEY": "compose-config-only-key",
            "ENVIRONMENT": "development",
        }
    )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "--project-directory",
            str(COMPOSE_PROJECT_DIR),
            "config",
        ],
        cwd=JOBPULSE_ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    expanded = result.stdout
    assert "change-this-knowledge-graph-jwt-secret" not in expanded
    assert "change-this-in-production-change-me" not in expanded
    expanded_services = yaml.safe_load(expanded)["services"]
    assert expanded_services["main-backend"]["environment"]["ENVIRONMENT"] == "development"
    assert (
        expanded_services["knowledge-graph-backend"]["environment"]["ENVIRONMENT"]
        == "production"
    )
    # Model-backed JD extraction stays gated behind its explicit profile.
    # CV extraction is gated behind its own profile; it joins the stack only
    # when CV_EXTRACTION_ENABLED=true activates the cv-extraction profile.
    assert "cv-extraction" not in expanded_services
    assert "jd-extraction" not in expanded_services

    profiled_result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "--project-directory",
            str(COMPOSE_PROJECT_DIR),
            "--profile",
            "model-extraction",
            "config",
        ],
        cwd=JOBPULSE_ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    assert profiled_result.returncode == 0, profiled_result.stderr
    profiled_services = yaml.safe_load(profiled_result.stdout)["services"]
    assert "jd-extraction" in profiled_services

    cv_profiled_result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "--project-directory",
            str(COMPOSE_PROJECT_DIR),
            "--profile",
            "cv-extraction",
            "config",
        ],
        cwd=JOBPULSE_ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    assert cv_profiled_result.returncode == 0, cv_profiled_result.stderr
    cv_profiled_services = yaml.safe_load(cv_profiled_result.stdout)["services"]
    assert "cv-extraction" in cv_profiled_services
    assert "cv-extraction-worker" in cv_profiled_services
    assert cv_profiled_services["cv-extraction-worker"]["environment"][
        "CV_EXTRACTION_WORKER_ENABLED"
    ] == "true"
    assert "jd-extraction" not in cv_profiled_services

    semantic_profiled_result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "--project-directory",
            str(COMPOSE_PROJECT_DIR),
            "--profile",
            "semantic-demo",
            "config",
        ],
        cwd=JOBPULSE_ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    assert semantic_profiled_result.returncode == 0, semantic_profiled_result.stderr
    semantic_services = yaml.safe_load(semantic_profiled_result.stdout)["services"]
    assert {"embedding-service", "qdrant", "matching-api-semantic-demo"} <= set(
        semantic_services
    )
    assert "matching-vector-worker-semantic-demo" in semantic_services
