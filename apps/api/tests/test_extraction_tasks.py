from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.dependencies.extraction_tasks import get_extraction_task_use_cases
from app.contexts.extraction_tasks import (
    ExtractionProviderError,
    ExtractionTaskConflict,
    ExtractionTaskRetryRejected,
    ExtractionTaskUseCases,
)
from app.contexts.source_jds import SourceJDUseCases
from app.infrastructure.extraction_tasks import (
    HttpJDExtractionProvider,
    RuleBasedJDExtractionProvider,
    SqlAlchemyExtractionTaskUnitOfWork,
)
from app.infrastructure.source_jds import SqlAlchemySourceJDUnitOfWork
from app.main import app
from app.models.extraction_task import ExtractionTask
from app.models.jd_parse_result import JDParseResult
from app.models.review_task import ReviewTask
from app.models.source_jd import SourceJDVersion
from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1
from jobgraph_contracts.extraction_bundle import ExtractedJDBundleV1
from jobgraph_contracts.extraction_v2 import JDExtractionResult
from jobgraph_contracts.normalization_v2 import JDNormalizedResult, JobClassification
from tests.runtime_database import SessionLocal, reset_database_data
from tests.user_factory import create_internal_user


NOW = datetime(2026, 7, 23, 12, tzinfo=timezone.utc)
client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_database():
    app.dependency_overrides.clear()
    reset_database_data()
    yield
    app.dependency_overrides.clear()
    reset_database_data()


def _envelope(
    raw_text: str = "Python backend role", *, source_version: str = "1"
) -> CrawlerJDEnvelopeV1:
    return CrawlerJDEnvelopeV1(
        source_platform="boss",
        source_record_id="job-task-1",
        source_version=source_version,
        source_url="https://example.test/job-task-1",
        crawl_time=NOW,
        raw_text=raw_text,
        raw_payload={"text": raw_text},
        raw_html=f"<p>{raw_text}</p>",
        job_title_raw="Backend Engineer",
        company_name_raw="Example",
        region_raw="Shanghai",
        publish_time_raw="today",
        text_canonicalization_version="raw-v1",
    )


def _bundle(envelope: CrawlerJDEnvelopeV1, **overrides) -> ExtractedJDBundleV1:
    document_id = f"{envelope.source_platform}:{envelope.source_record_id}"
    values = {
        "source_platform": envelope.source_platform,
        "source_record_id": envelope.source_record_id,
        "source_version": envelope.source_version,
        "cleaned_text": envelope.raw_text,
        "extraction_result": JDExtractionResult(document_id=document_id),
        "normalized_result": JDNormalizedResult(
            document_id=document_id,
            job_classification=JobClassification(
                classification_status="catalog_gap",
                review_reason_codes=["CLASSIFICATION_NOT_RUN"],
            ),
        ),
        "extraction_provider": "fake-deepseek",
        "model_version": "test-v1",
        "extraction_run_id": "run-1",
        "extraction_started_at": NOW,
        "extraction_finished_at": NOW + timedelta(seconds=1),
    }
    values.update(overrides)
    return ExtractedJDBundleV1(**values)


class FakeProvider:
    name = "fake"
    request_id = "fake-provider-request-v1"

    def __init__(self, outcomes=None, on_call=None):
        self.outcomes = list(outcomes or [])
        self.on_call = on_call
        self.calls = 0
        self.envelopes = []

    def extract(self, envelope):
        self.calls += 1
        self.envelopes.append(envelope)
        if self.on_call:
            self.on_call()
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return _bundle(envelope)


def _source_version(raw_text: str = "Python backend role", *, source_version: str = "1") -> str:
    result = SourceJDUseCases(
        lambda: SqlAlchemySourceJDUnitOfWork(SessionLocal)
    ).import_source_jd(_envelope(raw_text, source_version=source_version))
    return result.source_jd_version_id


def _use_cases(provider, max_attempts: int = 3) -> ExtractionTaskUseCases:
    return ExtractionTaskUseCases(
        lambda: SqlAlchemyExtractionTaskUnitOfWork(SessionLocal),
        provider,
        max_attempts,
        clock=lambda: NOW,
    )


def test_create_is_idempotent_per_version_and_request_id():
    version_id = _source_version()
    use_cases = _use_cases(FakeProvider())

    first = use_cases.create_extraction_task(version_id, "llm")
    repeated = use_cases.create_extraction_task(version_id, "llm")

    assert first.id == repeated.id
    assert first.status == "pending"
    assert first.attempt_count == 0
    with SessionLocal() as session:
        assert session.query(ExtractionTask).count() == 1


def test_rule_mode_persists_explicit_provider_metadata_and_review_gate():
    version_id = _source_version("职位：Python工程师\n负责 Python 开发")
    provider = RuleBasedJDExtractionProvider()
    use_cases = ExtractionTaskUseCases(
        lambda: SqlAlchemyExtractionTaskUnitOfWork(SessionLocal),
        {"rule": provider},
        1,
        clock=lambda: NOW,
    )

    task = use_cases.create_extraction_task(version_id, "rule")
    succeeded = use_cases.run_extraction_task(task.id)
    draft = use_cases.import_extraction_bundle(task.id)

    assert succeeded.extraction_mode == "rule"
    assert succeeded.provider == "rule_based_jd_extraction"
    assert succeeded.bundle_payload["execution"]["mode"] == "rule"
    assert succeeded.bundle_payload["need_review"] is True
    assert succeeded.bundle_payload["confidence_level"] == "limited"
    assert succeeded.bundle_payload["review_flags"][0]["code"] == (
        "RULE_BASED_EXTRACTION_REQUIRES_REVIEW"
    )
    assert draft.need_review is True
    assert draft.workflow_status == "draft"


def test_unconfigured_llm_fails_without_parse_result_or_review_task():
    version_id = _source_version()
    provider = HttpJDExtractionProvider(None, None, 1, 2)
    use_cases = ExtractionTaskUseCases(
        lambda: SqlAlchemyExtractionTaskUnitOfWork(SessionLocal),
        {"llm": provider},
        1,
        clock=lambda: NOW,
    )

    failed = use_cases.run_extraction_task(
        use_cases.create_extraction_task(version_id, "llm").id
    )

    assert failed.status == "failed"
    assert failed.bundle_payload is None
    assert failed.last_error_code == "extraction_provider_not_configured"
    with SessionLocal() as session:
        assert session.query(JDParseResult).count() == 0
        assert session.query(ReviewTask).count() == 0


def test_different_source_versions_create_independent_tasks():
    first_version = _source_version("first", source_version="1")
    second_version = _source_version("second", source_version="2")
    use_cases = _use_cases(FakeProvider())

    first = use_cases.create_extraction_task(first_version, "llm")
    second = use_cases.create_extraction_task(second_version, "llm")

    assert first.id != second.id
    assert first.source_jd_version_id != second.source_jd_version_id


def test_success_saves_complete_validated_bundle_and_is_idempotent():
    version_id = _source_version()
    provider = FakeProvider()
    use_cases = _use_cases(provider)
    task = use_cases.create_extraction_task(version_id, "llm")

    succeeded = use_cases.run_extraction_task(task.id)
    repeated = use_cases.run_extraction_task(task.id)

    assert succeeded.status == repeated.status == "succeeded"
    assert succeeded.bundle_payload is not None
    restored = ExtractedJDBundleV1.model_validate(dict(succeeded.bundle_payload))
    assert restored.source_version == _envelope().source_version
    assert provider.calls == 1


def test_identity_mismatch_is_failed_without_partial_bundle():
    version_id = _source_version()
    envelope = _envelope()
    provider = FakeProvider(
        [_bundle(envelope, source_record_id="different-source-record")]
    )
    use_cases = _use_cases(provider)

    failed = use_cases.run_extraction_task(
        use_cases.create_extraction_task(version_id, "llm").id
    )

    assert failed.status == "failed"
    assert failed.last_error_code == "extraction_bundle_contract_mismatch"
    assert failed.retryable is False
    assert failed.bundle_payload is None


def test_retryable_failure_retries_until_max_attempts():
    version_id = _source_version()
    failure = ExtractionProviderError(
        "extraction_timeout", "Extraction service timed out.", retryable=True
    )
    provider = FakeProvider([failure, failure])
    use_cases = _use_cases(provider, max_attempts=2)
    task = use_cases.create_extraction_task(version_id, "llm")

    first = use_cases.run_extraction_task(task.id)
    second = use_cases.retry_extraction_task(task.id)

    assert first.status == second.status == "failed"
    assert first.retryable is True
    assert second.retryable is False
    assert second.attempt_count == 2
    with pytest.raises(ExtractionTaskRetryRejected, match="not retryable|max attempts"):
        use_cases.retry_extraction_task(task.id)
    assert provider.calls == 2


def test_non_retryable_failure_cannot_retry():
    version_id = _source_version()
    failure = ExtractionProviderError(
        "extraction_contract_rejected", "Request rejected.", retryable=False
    )
    use_cases = _use_cases(FakeProvider([failure]))
    task = use_cases.create_extraction_task(version_id, "llm")
    failed = use_cases.run_extraction_task(task.id)

    assert failed.retryable is False
    with pytest.raises(ExtractionTaskRetryRejected, match="not retryable"):
        use_cases.retry_extraction_task(task.id)


def test_provider_error_details_are_not_persisted():
    version_id = _source_version()
    failure = ExtractionProviderError(
        "extraction_unavailable",
        "secret-token raw-jd model-response",
        retryable=True,
    )
    use_cases = _use_cases(FakeProvider([failure]))
    failed = use_cases.run_extraction_task(
        use_cases.create_extraction_task(version_id, "llm").id
    )
    assert failed.last_error_message == "Extraction provider reported a failure."
    assert "secret-token" not in failed.last_error_message


def test_remote_call_holds_no_database_transaction():
    version_id = _source_version()

    def assert_database_is_unlocked():
        with SessionLocal() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            session.rollback()

    provider = FakeProvider(on_call=assert_database_is_unlocked)
    use_cases = _use_cases(provider)
    result = use_cases.run_extraction_task(
        use_cases.create_extraction_task(version_id, "llm").id
    )
    assert result.status == "succeeded"


def test_concurrent_run_only_invokes_provider_once():
    version_id = _source_version()
    entered = threading.Event()
    release = threading.Event()

    def block_provider():
        entered.set()
        assert release.wait(timeout=10)

    provider = FakeProvider(on_call=block_provider)
    use_cases = _use_cases(provider)
    task = use_cases.create_extraction_task(version_id, "llm")
    outcomes = []

    def run():
        try:
            outcomes.append(use_cases.run_extraction_task(task.id).status)
        except ExtractionTaskConflict:
            outcomes.append("conflict")

    first = threading.Thread(target=run)
    second = threading.Thread(target=run)
    first.start()
    assert entered.wait(timeout=10)
    second.start()
    second.join(timeout=10)
    release.set()
    first.join(timeout=10)

    assert not first.is_alive() and not second.is_alive()
    assert sorted(outcomes) == ["conflict", "succeeded"]
    assert provider.calls == 1


@pytest.mark.parametrize(
    ("handler", "expected_code", "retryable"),
    [
        (
            lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("timeout")),
            "extraction_timeout",
            True,
        ),
        (
            lambda request: httpx.Response(
                503,
                json={
                    "code": 503,
                    "message": "safe",
                    "data": {"error_code": "model_unavailable", "retryable": True},
                },
            ),
            "extraction_unavailable",
            True,
        ),
        (
            lambda request: httpx.Response(
                422,
                json={
                    "code": 422,
                    "message": "safe",
                    "data": {"error_code": "schema_validation_failed", "retryable": False},
                },
            ),
            "extraction_contract_rejected",
            False,
        ),
    ],
)
def test_http_provider_maps_stable_errors(handler, expected_code, retryable):
    provider = HttpJDExtractionProvider(
        "https://extraction.test",
        "a-strong-internal-token-with-32-characters",
        1,
        2,
        transport=httpx.MockTransport(handler),
        model_service_config=lambda: (
            "https://model.test",
            "deepseek-test",
            "sk-runtime-secret",
        ),
    )
    with pytest.raises(ExtractionProviderError) as captured:
        provider.extract(_envelope())
    assert captured.value.code == expected_code
    assert captured.value.retryable is retryable


def test_http_provider_posts_envelope_with_internal_token_and_parses_bundle():
    envelope = _envelope()
    payload = _bundle(envelope).model_dump(mode="json")
    payload["schema_version"] = "extracted-jd-bundle-v2"
    payload["skill_taxonomy"] = {
        "schema_version": "skill-taxonomy-projection.v1",
        "taxonomy_version": "skill-taxonomy-snapshot.v1",
        "skills": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/extractions"
        assert request.headers["Authorization"] == (
            "Bearer a-strong-internal-token-with-32-characters"
        )
        assert request.headers["X-JobPulse-Model-Base-URL"] == "https://model.test"
        assert request.headers["X-JobPulse-Model-Name"] == "deepseek-test"
        assert request.headers["X-JobPulse-Model-API-Key"] == "sk-runtime-secret"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "success",
                "data": payload,
            },
        )

    provider = HttpJDExtractionProvider(
        "https://extraction.test",
        "a-strong-internal-token-with-32-characters",
        1,
        2,
        transport=httpx.MockTransport(handler),
        model_service_config=lambda: (
            "https://model.test",
            "deepseek-test",
            "sk-runtime-secret",
        ),
    )
    result = provider.extract(envelope)
    assert result.source_version == envelope.source_version


def _headers(role: str = "developer") -> dict[str, str]:
    suffix = uuid4().hex[:8]
    username = f"extract_{role}_{suffix}"
    create_internal_user(username, role)
    login = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "password123"},
    )
    return {"Authorization": f"Bearer {login.json()['data']['access_token']}"}


def test_api_requires_auth_and_uses_injected_fake_provider():
    version_id = _source_version()
    use_cases = _use_cases(FakeProvider())
    app.dependency_overrides[get_extraction_task_use_cases] = lambda: use_cases
    path = f"/api/v1/source-jd-versions/{version_id}/extraction-tasks"

    params = {"extraction_mode": "llm"}
    assert client.post(path, headers=_headers()).status_code == 422
    assert client.post(path, params=params).status_code == 401
    headers = _headers()
    created = client.post(path, params=params, headers=headers)
    assert created.status_code == 200
    task_id = created.json()["data"]["id"]
    run = client.post(f"/api/v1/extraction-tasks/{task_id}/run", headers=headers)
    fetched = client.get(f"/api/v1/extraction-tasks/{task_id}", headers=headers)
    assert run.status_code == fetched.status_code == 200
    assert fetched.json()["data"]["status"] == "succeeded"


def test_extraction_task_api_permission_boundary():
    version_id = _source_version()
    use_cases = _use_cases(FakeProvider())
    app.dependency_overrides[get_extraction_task_use_cases] = lambda: use_cases
    path = f"/api/v1/source-jd-versions/{version_id}/extraction-tasks"

    personal = _headers("personal_user")
    reviewer = _headers("reviewer")
    developer = _headers("developer")

    params = {"extraction_mode": "llm"}
    assert client.post(path, params=params, headers=personal).status_code == 403
    assert client.post(path, params=params, headers=reviewer).status_code == 403

    created = client.post(path, params=params, headers=developer)
    assert created.status_code == 200
    task_id = created.json()["data"]["id"]

    # Reviewer仅能读取运维任务，不允许改变全局任务状态。
    assert (
        client.get(f"/api/v1/extraction-tasks/{task_id}", headers=reviewer).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/extraction-tasks/{task_id}/run", headers=reviewer
        ).status_code
        == 403
    )


def test_source_version_remains_unchanged_after_extraction():
    version_id = _source_version()
    with SessionLocal() as session:
        before = session.get(SourceJDVersion, version_id).raw_text
    use_cases = _use_cases(FakeProvider())
    use_cases.run_extraction_task(
        use_cases.create_extraction_task(version_id, "llm").id
    )
    with SessionLocal() as session:
        assert session.get(SourceJDVersion, version_id).raw_text == before


def test_readiness_reports_optional_extraction_integration_not_configured():
    response = client.get("/readiness")
    assert response.status_code == 200
    check = response.json()["data"]["checks"]["jd_extraction"]
    assert check["rule"] == {
        "ready": True,
        "provider": "rule_based_jd_extraction",
        "requires_review": True,
    }
    assert check["llm"] == {
        "ready": False,
        "provider": "http_jd_extraction",
        "optional": True,
        "error_code": "extraction_not_configured",
    }
