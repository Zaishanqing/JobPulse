from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
from pydantic import ValidationError

from app.acquisition.api.acquisition_router import _public_source, acquisition_router
from app.acquisition.api.acquisition_schemas import CreateBundleRequest, CreateCrawlJobRequest
from app.acquisition.application.crawl_service import CrawlService
from app.acquisition.infrastructure.connectors import (
    ArxivConnector,
    ConnectorRegistry,
    PolicyConnector,
)
from app.acquisition.infrastructure.rate_limiter import RateLimiter


UTC = timezone.utc
START = datetime(2026, 1, 1, tzinfo=UTC)
END = datetime(2026, 2, 1, tzinfo=UTC)
CONFIGURATIONS = {
    "domain_dictionary": {"人工智能": ["artificial intelligence", "人工智能"]},
    "policy_keywords": {"queries": ["人工智能"]},
}


class FakeStore:
    def __init__(self, source_type: str, *, max_retries: int = 0) -> None:
        self.source = {
            "id": "source-1",
            "source_type": source_type,
            "endpoint_config": {},
            "rate_limit_rps": 100.0,
        }
        self.job = {
            "id": "job-1",
            "source_id": "source-1",
            "status": "pending",
            "window_start": START,
            "window_end": END,
            "retry_count": 0,
            "max_retries": max_retries,
            "error_message": None,
        }
        self.complete_calls = 0

    def recover_expired_crawl_jobs(self, *, now):
        return 0

    def claim_crawl_job(self, worker_id, *, now, lease):
        if self.job["status"] != "pending":
            return None
        self.job["status"] = "running"
        return dict(self.job)

    def get_crawl_job(self, job_id):
        return dict(self.job) if job_id == self.job["id"] else None

    def get_source(self, source_id):
        return dict(self.source) if source_id == self.source["id"] else None

    def mark_job_running(self, job_id):
        if job_id != self.job["id"] or self.job["status"] != "pending":
            return False
        self.job["status"] = "running"
        return True

    def mark_job_failed(self, job_id, error, *, retryable=True):
        self.job["error_message"] = error
        if retryable and self.job["retry_count"] < self.job["max_retries"]:
            self.job["retry_count"] += 1
            self.job["status"] = "pending"
        else:
            self.job["status"] = "failed"
        return True

    def complete_crawl_job(self, job_id, source_id, records):
        self.complete_calls += 1
        self.job["status"] = "succeeded"
        return records


class EmptyConnector:
    def fetch(self, source, window_start, window_end):
        return []


class FailingConnector:
    def fetch(self, source, window_start, window_end):
        raise httpx.ConnectError("upstream unavailable")


def test_manual_bundle_contract_requires_explicit_job_id():
    request = CreateBundleRequest(
        job_id="job-real",
        snapshot_ids=["snapshot-first"],
        bundle_type="raw_snapshot",
    )
    assert request.job_id == "job-real"
    assert request.job_id != request.snapshot_ids[0]
    with pytest.raises(ValidationError):
        CreateBundleRequest(
            snapshot_ids=["must-not-be-treated-as-job"],
            bundle_type="raw_snapshot",
        )


def test_source_api_projection_never_exposes_auth_values():
    public = _public_source({
        "id": "source-1",
        "name": "secured source",
        "endpoint_config": {
            "endpoint": "https://example.test/data",
            "headers": {
                "Authorization": "Bearer endpoint-secret",
                "X-Api-Key": "endpoint-api-key",
            },
            "client_secret": "endpoint-client-secret",
        },
        "auth_config": {
            "token": "auth-token",
            "username": "private-user",
        },
    })
    assert public["auth_configured"] is True
    assert "auth_config" not in public
    assert public["endpoint_config"] == {
        "endpoint": "https://example.test/data",
        "headers": {},
    }
    assert "secret" not in str(public).lower()


def test_schedule_contract_is_rejected_and_cancel_is_present():
    with pytest.raises(ValidationError):
        CreateCrawlJobRequest.model_validate({
            "source_id": "source-1",
            "window_start": "2026-01-01T00:00:00Z",
            "window_end": "2026-02-01T00:00:00Z",
            "schedule": {"cron": "0 * * * *"},
        })
    paths = {route.path for route in acquisition_router.routes}
    assert "/internal/v1/acquisition/crawl-jobs/{job_id}/cancel" in paths
    assert "/internal/v1/acquisition/crawl-jobs/{job_id}/retry" in paths


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_arxiv_connector_uses_mock_http_real_pagination_and_source_rate_limit(monkeypatch):
    pages = {
        "0": """<feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
          <opensearch:totalResults>2</opensearch:totalResults><entry>
          <id>https://arxiv.org/abs/2601.00001</id><title>Artificial Intelligence One</title>
          <summary>First abstract.</summary><published>2026-01-10T00:00:00Z</published>
          <category term="cs.AI" /></entry></feed>""",
        "1": """<feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
          <opensearch:totalResults>2</opensearch:totalResults><entry>
          <id>https://arxiv.org/abs/2601.00002</id><title>Artificial Intelligence Two</title>
          <summary>Second abstract.</summary><published>2026-01-11T00:00:00Z</published>
          <category term="cs.LG" /></entry></feed>""",
    }
    requests: list[str] = []
    limited: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        start = request.url.params.get("start", "0")
        requests.append(start)
        return httpx.Response(200, text=pages[start])

    monkeypatch.setattr(RateLimiter, "acquire", lambda self: limited.append(self.rate))
    connector = ArxivConnector(client(handler), CONFIGURATIONS, default_limit=10)
    records = connector.fetch(
        {
            "id": "source-arxiv",
            "source_type": "arxiv",
            "endpoint_config": {"limit": 10},
            "rate_limit_rps": 7.0,
        },
        START,
        END,
    )

    assert requests == ["0", "1"]
    assert limited == [7.0, 7.0]
    assert [record.external_id for record in records] == ["2601.00001", "2601.00002"]
    assert records[0].raw_content["source"] == "arxiv"


def test_policy_connector_uses_mock_http_and_limits_each_query(monkeypatch):
    requested_queries: list[str] = []
    limited: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params["q"]
        requested_queries.append(query)
        return httpx.Response(200, json={
            "searchVO": {"catMap": {"政策": {"listVO": [{
                "url": f"https://gov.example/{query}",
                "title": f"{query}发展政策",
                "pubtimeStr": "2026.01.12",
                "summary": "推动人工智能产业发展",
                "puborg": "国务院",
            }]}}},
        })

    monkeypatch.setattr(RateLimiter, "acquire", lambda self: limited.append(self.rate))
    connector = PolicyConnector(client(handler), CONFIGURATIONS)
    records = connector.fetch(
        {
            "id": "source-policy",
            "source_type": "policy",
            "endpoint_config": {"queries": ["人工智能", "数据安全"], "per_query": 5},
            "rate_limit_rps": 9.0,
        },
        START,
        END,
    )

    assert requested_queries == ["人工智能", "数据安全"]
    assert limited == [9.0, 9.0]
    assert len(records) == 2
    assert all(record.raw_content["source"] == "policy" for record in records)


def test_worker_claims_pending_job_and_unknown_connector_fails_terminally():
    store = FakeStore("unknown", max_retries=3)
    service = CrawlService(store, registry=ConnectorRegistry())

    assert service.run_once("worker-1") is True
    assert store.job["status"] == "failed"
    assert store.job["retry_count"] == 0
    assert "no connector registered" in str(store.job["error_message"])
    assert store.complete_calls == 0


def test_empty_connector_and_request_failures_never_succeed_and_retry_is_preserved():
    empty_store = FakeStore("empty")
    empty_service = CrawlService(
        empty_store,
        registry=ConnectorRegistry({"empty": EmptyConnector()}),
    )
    empty_result = empty_service.execute_job("job-1")
    assert empty_result["status"] == "failed"
    assert "no records" in str(empty_result["error_message"])
    assert empty_store.complete_calls == 0

    failing_store = FakeStore("failing", max_retries=1)
    failing_service = CrawlService(
        failing_store,
        registry=ConnectorRegistry({"failing": FailingConnector()}),
    )
    first = failing_service.execute_job("job-1")
    assert first["status"] == "pending"
    assert first["retry_count"] == 1
    second = failing_service.execute_job("job-1")
    assert second["status"] == "failed"
    assert "ConnectError" in str(second["error_message"])
    assert failing_store.complete_calls == 0
