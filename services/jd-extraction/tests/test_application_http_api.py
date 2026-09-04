from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1

from src.api.settings import ExtractionAPISettings
from src.application.errors import ExtractionErrorCode, JDExtractionApplicationError
from src.application.extraction_service import JDExtractionApplicationService
from src.main import create_app

from application_fakes import FakeClient, FakePositionClassifier, valid_payload


TOKEN = "test-internal-token-without-default"
NORMALIZATION_PATH = str(Path("config/normalization_map.yaml"))


def envelope(record_id: str = "job-1") -> CrawlerJDEnvelopeV1:
    raw_text = "Python开发工程师；熟练使用 Python"
    return CrawlerJDEnvelopeV1(
        source_record_id=record_id,
        source_platform="boss_zhipin",
        crawl_time=datetime.now(timezone.utc),
        raw_text=raw_text,
        raw_payload={"record": record_id},
        text_canonicalization_version="v1",
        source_version="1",
    )


def settings(
    *,
    token: str | None = TOKEN,
    concurrency: int = 4,
    request_bytes: int = 2 * 1024 * 1024,
) -> ExtractionAPISettings:
    errors = () if token is not None else ("JD_EXTRACTION_INTERNAL_TOKEN is required",)
    return ExtractionAPISettings(
        internal_token=token,
        model="fake-model",
        normalization_path=NORMALIZATION_PATH,
        extraction_provider="fake",
        max_concurrency=concurrency,
        max_request_bytes=request_bytes,
        configuration_errors=errors,
    )


def application_service(fake: FakeClient | None = None) -> JDExtractionApplicationService:
    payload = valid_payload()
    payload["job_title"] = {
        "value": "Python开发工程师",
        "evidence": {
            "source_id": "src_0001",
            "quote": "Python开发工程师",
        },
    }
    payload["requirements"][0]["evidence"] = {
        "source_id": "src_0002",
        "quote": "熟练使用 Python",
    }
    return JDExtractionApplicationService(
        model="fake-model",
        normalization_path=NORMALIZATION_PATH,
        client=fake or FakeClient(payload),
        position_classifier=FakePositionClassifier(),
        extraction_provider="fake",
        extraction_run_id="http-test",
        semantic_retry_attempts=0,
    )


def client(service=None, api_settings=None) -> TestClient:
    app = create_app(
        settings=api_settings or settings(),
        extraction_service=service or application_service(),
    )
    return TestClient(app)


def auth_headers(**extra):
    return {"Authorization": f"Bearer {TOKEN}", **extra}


def test_single_success_uses_unified_response_and_request_id():
    with client() as http:
        response = http.post(
            "/api/v2/extractions",
            json=envelope().model_dump(mode="json"),
            headers=auth_headers(**{"X-Request-ID": "request-123"}),
        )
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "request-123"
    payload = response.json()
    assert payload["code"] == 0
    assert payload["message"] == "success"
    assert payload["data"]["source_record_id"] == "job-1"
    assert payload["data"]["schema_version"] == "extracted-jd-bundle-v2"
    classification = payload["data"]["normalized_result"]["job_classification"]
    assert classification["classification_status"] == "resolved"
    assert classification["position_code"] == "BACKEND_ENGINEER"
    assert "CLASSIFICATION_NOT_RUN" not in classification["review_reason_codes"]


def test_request_model_configuration_builds_and_uses_the_configured_service():
    calls = []
    dynamic_service = application_service()

    def factory(base_url: str, model: str, api_key: str):
        calls.append((base_url, model, api_key))
        return dynamic_service

    app = create_app(
        settings=settings(),
        extraction_service=application_service(),
        model_service_factory=factory,
    )
    headers = auth_headers(
        **{
            "X-JobPulse-Model-Base-URL": "https://model.test",
            "X-JobPulse-Model-Name": "deepseek-test",
            "X-JobPulse-Model-API-Key": "sk-runtime-secret",
        }
    )
    with TestClient(app) as http:
        response = http.post(
            "/api/v2/extractions",
            json=envelope().model_dump(mode="json"),
            headers=headers,
        )
    assert response.status_code == 200
    assert calls == [("https://model.test", "deepseek-test", "sk-runtime-secret")]


def test_partial_request_model_configuration_is_rejected_before_extraction():
    with client() as http:
        response = http.post(
            "/api/v2/extractions",
            json=envelope().model_dump(mode="json"),
            headers=auth_headers(**{"X-JobPulse-Model-Name": "deepseek-test"}),
        )
    assert response.status_code == 400
    assert response.json()["data"]["error_code"] == "model_configuration_invalid"


def test_v2_single_response_includes_approved_taxonomy_projection():
    with client() as http:
        response = http.post(
            "/api/v2/extractions",
            json=envelope().model_dump(mode="json"),
            headers=auth_headers(),
        )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["schema_version"] == "extracted-jd-bundle-v2"
    assert data["skill_taxonomy"]["schema_version"] == (
        "skill-taxonomy-projection.v1"
    )
    assert data["skill_taxonomy"]["taxonomy_version"] == "skill-taxonomy-snapshot.v1"


def test_invalid_envelope_is_wrapped_without_validation_details():
    invalid = envelope().model_dump(mode="json")
    invalid.pop("raw_text")
    with client() as http:
        response = http.post("/api/v2/extractions", json=invalid, headers=auth_headers())
    assert response.status_code == 422
    assert response.json()["data"]["error_code"] == "invalid_envelope"
    assert "raw_text" not in response.text


@pytest.mark.parametrize("failure", ["missing_field"])
def test_single_contract_failures_are_classified_without_calling_service(failure: str):
    class ForbiddenService:
        calls = 0

        def extract_one_v2(self, value):
            self.calls += 1
            raise AssertionError("invalid request must not reach extraction")

    service = ForbiddenService()
    invalid = envelope().model_dump(mode="json")
    expected = "invalid_envelope"
    invalid.pop("raw_text")
    with client(service) as http:
        response = http.post("/api/v2/extractions", json=invalid, headers=auth_headers())
    assert response.status_code == 422
    assert response.json()["data"]["error_code"] == expected
    assert service.calls == 0


@pytest.mark.parametrize(
    ("authorization", "status"),
    [(None, 401), ("Basic abc", 401), ("Bearer wrong", 401), (f"Bearer {TOKEN}", 200)],
)
def test_bearer_authentication(authorization: str | None, status: int):
    headers = {} if authorization is None else {"Authorization": authorization}
    with client() as http:
        response = http.post(
            "/api/v2/extractions",
            json=envelope().model_dump(mode="json"),
            headers=headers,
        )
    assert response.status_code == status
    if status == 401:
        assert response.json()["data"]["error_code"] == "unauthorized"


@pytest.mark.parametrize(
    ("error_code", "status", "retryable"),
    [
        (ExtractionErrorCode.INVALID_ENVELOPE, 422, False),
        (ExtractionErrorCode.MODEL_UNAVAILABLE, 503, True),
        (ExtractionErrorCode.MODEL_TIMEOUT, 504, True),
        (ExtractionErrorCode.MODEL_INVALID_RESPONSE, 502, True),
        (ExtractionErrorCode.SCHEMA_VALIDATION_FAILED, 422, False),
        (ExtractionErrorCode.EVIDENCE_VALIDATION_FAILED, 422, False),
        (ExtractionErrorCode.SEMANTIC_VALIDATION_FAILED, 422, False),
        (ExtractionErrorCode.BUSINESS_VALIDATION_FAILED, 422, False),
        (ExtractionErrorCode.NORMALIZATION_FAILED, 500, False),
        (ExtractionErrorCode.CONTRACT_VALIDATION_FAILED, 500, False),
        (ExtractionErrorCode.INTERNAL_ERROR, 500, False),
    ],
)
def test_application_errors_map_by_type_code(error_code, status, retryable):
    class FailingService:
        def extract_one_v2(self, value):
            raise JDExtractionApplicationError(error_code, "safe public message")

    with client(FailingService()) as http:
        response = http.post(
            "/api/v2/extractions",
            json=envelope().model_dump(mode="json"),
            headers=auth_headers(),
        )
    assert response.status_code == status
    assert response.json()["data"] == {
        "error_code": error_code.value,
        "retryable": retryable,
        "request_id": response.headers["X-Request-ID"],
    }


def test_health_never_calls_extraction_or_model():
    class ForbiddenService:
        def extract_one(self, value):
            raise AssertionError("health must not call extraction")

    with client(ForbiddenService()) as http:
        response = http.get("/health")
    assert response.status_code == 200
    assert response.json()["data"] == {"status": "alive"}


def test_readiness_succeeds_without_model_probe():
    class ReadyService:
        def extract_one(self, value):
            raise AssertionError("readiness must not call extraction")

    with client(ReadyService()) as http:
        response = http.get("/readiness")
    assert response.status_code == 200
    assert response.json()["data"]["ready"] is True


def test_readiness_fails_when_token_is_not_configured():
    with client(application_service(), settings(token=None)) as http:
        response = http.get("/readiness")
        business = http.post(
            "/api/v2/extractions",
            json=envelope().model_dump(mode="json"),
        )
    assert response.status_code == 503
    assert response.json()["data"]["error_code"] == "service_not_ready"
    assert business.status_code == 503


def test_request_body_limit_is_enforced():
    with client(api_settings=settings(request_bytes=64)) as http:
        response = http.post(
            "/api/v2/extractions",
            json=envelope().model_dump(mode="json"),
            headers=auth_headers(),
        )
    assert response.status_code == 413
    assert response.json()["data"]["error_code"] == "request_too_large"


def test_http_fake_injection_never_constructs_real_deepseek(monkeypatch):
    def forbid_real_client(*args, **kwargs):
        raise AssertionError("real DeepSeek client must not be constructed")

    monkeypatch.setattr("src.pipeline.DeepSeekClient", forbid_real_client)
    payload = valid_payload()
    payload["job_title"] = {
        "value": "Python开发工程师",
        "evidence": {
            "source_id": "src_0001",
            "quote": "Python开发工程师",
        },
    }
    payload["requirements"][0]["evidence"] = {
        "source_id": "src_0002",
        "quote": "熟练使用 Python",
    }
    fake = FakeClient(payload)
    injected = application_service(fake)
    with client(injected) as http:
        response = http.post(
            "/api/v2/extractions",
            json=envelope().model_dump(mode="json"),
            headers=auth_headers(),
        )
    assert response.status_code == 200
    assert fake.calls == 1


def test_dockerfile_has_non_root_runtime_healthcheck_and_safe_context():
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (root / "Dockerfile.dockerignore").read_text(encoding="utf-8")
    assert "FROM python:3.11" in dockerfile
    assert "USER extraction" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "uvicorn" in dockerfile
    assert "src.main:app" in dockerfile
    for forbidden in ("**/.env", "**/tests/", "**/output/", "**/audit/"):
        assert forbidden in dockerignore
