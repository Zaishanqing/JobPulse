import json
import logging

import httpx
from fastapi.testclient import TestClient

from app.core.logging import JsonFormatter
from app import main as main_module
from app.api.v1 import observability
from app.main import app
from app.main import create_app
from app.api.dependencies.accounts import get_account_actor
from app.core.config import Settings
from app.core.database import Base
from app.domain.accounts import AccountActor
from app.infrastructure import readiness


client = TestClient(app)


def test_health_returns_200():
    response = client.get("/health")

    assert response.status_code == 200


def test_health_response_format():
    response = client.get("/health")
    payload = response.json()

    assert set(payload.keys()) == {"code", "message", "data", "trace_id"}
    assert payload["code"] == 0
    assert payload["message"] == "success"
    assert payload["data"] == {"status": "ok"}


def test_api_v1_health_is_registered():
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["code"] == 0


def test_root_and_api_readiness_verify_database():
    for path in ("/readiness", "/api/v1/readiness"):
        response = client.get(path, headers={"X-Request-ID": "req_readiness-test"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["data"] == {
            "status": "ready",
            "checks": {
                "database": {"ready": True},
                "jd_extraction": {
                    "rule": {
                        "ready": True,
                        "provider": "rule_based_jd_extraction",
                        "requires_review": True,
                    },
                    "llm": {
                        "ready": False,
                        "provider": "http_jd_extraction",
                        "optional": True,
                        "error_code": "extraction_not_configured",
                    },
                },
            },
            "configuration": {
                "data_validation_mode": "off",
                "crawler_data_exchange": "offline_bundle",
                "cv_extraction_enabled": False,
            },
        }
        assert payload["trace_id"] == "req_readiness-test"


def test_readiness_probes_configured_extraction_service(monkeypatch):
    calls = []

    def ready(url, *, timeout):
        calls.append((url, timeout))
        return httpx.Response(200, request=httpx.Request("GET", url))

    monkeypatch.setattr("app.infrastructure.system.httpx.get", ready)
    configured = Settings(
        DATABASE_URL="sqlite:///:memory:",
        JD_EXTRACTION_BASE_URL="http://jd-extraction:8000/",
        JD_EXTRACTION_INTERNAL_TOKEN="strong-extraction-token-with-32-characters",
        _env_file=None,
    )
    with TestClient(create_app(configured)) as configured_client:
        response = configured_client.get("/readiness")

    assert response.status_code == 200
    assert response.json()["data"]["checks"]["jd_extraction"] == {
        "rule": {
            "ready": True,
            "provider": "rule_based_jd_extraction",
            "requires_review": True,
        },
        "llm": {
            "ready": True,
            "provider": "http_jd_extraction",
            "optional": True,
            "error_code": None,
        },
    }
    assert calls[0][0] == "http://jd-extraction:8000/readiness"


def test_unavailable_extraction_is_reported_without_failing_main_readiness(monkeypatch):
    def unavailable(url, *, timeout):
        raise httpx.ConnectError("sensitive upstream detail")

    monkeypatch.setattr("app.infrastructure.system.httpx.get", unavailable)
    configured = Settings(
        DATABASE_URL="sqlite:///:memory:",
        JD_EXTRACTION_BASE_URL="http://jd-extraction:8000",
        JD_EXTRACTION_INTERNAL_TOKEN="strong-extraction-token-with-32-characters",
        _env_file=None,
    )
    with TestClient(create_app(configured)) as configured_client:
        response = configured_client.get("/readiness")

    assert response.status_code == 200
    extraction = response.json()["data"]["checks"]["jd_extraction"]
    assert extraction == {
        "rule": {
            "ready": True,
            "provider": "rule_based_jd_extraction",
            "requires_review": True,
        },
        "llm": {
            "ready": False,
            "provider": "http_jd_extraction",
            "optional": True,
            "error_code": "extraction_unavailable",
        },
    }
    assert "sensitive" not in response.text


def test_readiness_displays_configured_validation_controls():
    configured = Settings(
        DATABASE_URL="sqlite:///:memory:",
        DATA_VALIDATION_MODE="observe",
        CV_EXTRACTION_ENABLED=True,
        CV_EXTRACTION_BASE_URL="http://cv-extraction:8000",
        CV_EXTRACTION_INTERNAL_TOKEN="test-cv-extraction-internal-token-at-least-32-characters",
        _env_file=None,
    )
    with TestClient(create_app(configured)) as configured_client:
        response = configured_client.get("/readiness")

    assert response.status_code == 200
    assert response.json()["data"]["configuration"] == {
        "data_validation_mode": "observe",
        "crawler_data_exchange": "offline_bundle",
        "cv_extraction_enabled": True,
    }


def test_readiness_and_system_status_share_the_same_runtime_configuration(tmp_path):
    configured = Settings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'status.db').as_posix()}",
        DATA_VALIDATION_MODE="enforce",
        CV_EXTRACTION_ENABLED="false",
        _env_file=None,
    )
    application = create_app(configured)
    application.dependency_overrides[get_account_actor] = lambda: AccountActor(
        "config-status-admin", "admin"
    )
    try:
        with TestClient(application) as configured_client:
            Base.metadata.create_all(
                bind=application.state.container.system.status._database.engine
            )
            root_readiness = configured_client.get("/readiness")
            api_readiness = configured_client.get("/api/v1/readiness")
            system_status = configured_client.get("/api/v1/system/status")
    finally:
        application.dependency_overrides.clear()

    expected = {
        "data_validation_mode": "enforce",
        "crawler_data_exchange": "offline_bundle",
        "cv_extraction_enabled": False,
    }
    assert root_readiness.status_code == 200
    assert api_readiness.status_code == 200
    assert system_status.status_code == 200
    assert root_readiness.json()["data"]["configuration"] == expected
    assert api_readiness.json()["data"]["configuration"] == expected
    assert system_status.json()["data"]["configuration"] == expected
    assert {"status", "checks", "configuration"} <= root_readiness.json()["data"].keys()
    assert {"status", "components", "configuration"} <= system_status.json()["data"].keys()


def test_json_log_formatter_emits_safe_request_metadata_only():
    record = logging.LogRecord(
        "app.main", logging.INFO, __file__, 1, "request_completed", (), None
    )
    record.trace_id = "req_log-test"
    record.method = "POST"
    record.path = "/api/v1/auth/login"
    record.status_code = 200
    record.duration_ms = 1.25
    record.authorization = "Bearer secret-token"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["trace_id"] == "req_log-test"
    assert payload["status_code"] == 200
    assert payload["duration_ms"] == 1.25
    assert "authorization" not in payload
    assert "secret-token" not in json.dumps(payload)


def test_readiness_reports_database_failure_without_internal_error(monkeypatch):
    class BrokenEngine:
        def connect(self):
            from sqlalchemy.exc import SQLAlchemyError

            raise SQLAlchemyError("secret database location")

    ready, data = readiness.check_readiness(BrokenEngine())

    assert ready is False
    assert data == {"status": "not_ready", "checks": {"database": {"ready": False}}}
    assert "secret" not in str(data)


def test_root_and_api_readiness_return_sanitized_503_on_database_failure(monkeypatch):
    unavailable = (
        False,
        {
            "status": "not_ready",
            "checks": {"database": {"ready": False}},
            "configuration": {
                "data_validation_mode": "off",
                "crawler_data_exchange": "offline_bundle",
                "cv_extraction_enabled": False,
            },
        },
    )
    monkeypatch.setattr(main_module, "check_readiness", lambda _engine: unavailable)
    monkeypatch.setattr(observability, "check_readiness", lambda _engine: unavailable)

    for path in ("/readiness", "/api/v1/readiness"):
        response = client.get(path, headers={"X-Request-ID": "req_failed-readiness"})
        payload = response.json()
        assert response.status_code == 503
        assert payload == {
            "code": 503,
            "message": "Service is not ready",
            "data": unavailable[1],
            "trace_id": "req_failed-readiness",
        }
        assert response.headers["X-Request-ID"] == "req_failed-readiness"
        assert "password" not in response.text.lower()
        assert "traceback" not in response.text.lower()


def test_trace_id_is_stable_across_header_and_response_body():
    supplied_trace_id = "req_client-123"
    response = client.get("/health", headers={"X-Request-ID": supplied_trace_id})

    assert response.headers["X-Request-ID"] == supplied_trace_id
    assert response.json()["trace_id"] == supplied_trace_id


def test_invalid_trace_id_is_replaced_and_errors_are_traceable():
    response = client.get(
        "/api/v1/auth/me",
        headers={"X-Request-ID": "invalid trace id with spaces"},
    )

    trace_id = response.json()["trace_id"]
    assert response.status_code == 401
    assert trace_id.startswith("req_")
    assert trace_id != "invalid trace id with spaces"
    assert response.headers["X-Request-ID"] == trace_id


def test_unhandled_error_is_sanitized_and_traceable():
    route_count = len(app.router.routes)

    async def raise_unhandled_error():
        raise RuntimeError("sensitive database path")

    app.add_api_route("/__test__/unhandled", raise_unhandled_error, methods=["GET"])
    no_raise_client = TestClient(app, raise_server_exceptions=False)
    try:
        response = no_raise_client.get("/__test__/unhandled")
    finally:
        del app.router.routes[route_count:]
        app.openapi_schema = None

    payload = response.json()
    assert response.status_code == 500
    assert payload["message"] == "Internal server error"
    assert "sensitive database path" not in response.text
    assert payload["trace_id"] == response.headers["X-Request-ID"]
