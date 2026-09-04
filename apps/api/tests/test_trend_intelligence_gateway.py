from __future__ import annotations

import httpx
import pytest
from datetime import datetime, timezone

from app.contexts.market_intelligence._ports.trend_intelligence_gateway_v1 import (
    TrendIntelligenceGatewayError,
)
from app.contexts.market_intelligence._ports.position_skill_trend_gateway_v1 import (
    CreatePositionSkillTrendV1,
)
from app.domain.json_types import freeze_json_object
from app.core.config import Settings
from app.infrastructure.trend_intelligence_gateway import (
    HttpTrendIntelligenceGatewayV1,
    TrendIntelligenceHttpClient,
)


def test_http_client_sends_bearer_token_timeout_and_retries() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        assert request.headers["Authorization"] == "Bearer internal-secret"
        if attempts == 1:
            return httpx.Response(503, json={"detail": "temporarily unavailable"})
        return httpx.Response(200, json={"data": {"id": "run-1", "status": "pending"}})

    client = TrendIntelligenceHttpClient(
        base_url="http://trend.test",
        token="internal-secret",
        timeout_seconds=7,
        max_retries=1,
        retry_backoff_seconds=0,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleeper=lambda _: None,
    )

    assert client.request("GET", "/internal/v1/analysis-runs/run-1")["id"] == "run-1"
    assert client.timeout_seconds == 7
    assert attempts == 2


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "TREND_INTELLIGENCE_AUTHENTICATION_FAILED"),
        (404, "TREND_INTELLIGENCE_RUN_NOT_FOUND"),
        (422, "TREND_INTELLIGENCE_INVALID_REQUEST"),
        (503, "TREND_INTELLIGENCE_UPSTREAM_ERROR"),
    ],
)
def test_http_client_maps_structured_errors(status: int, code: str) -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(status, json={"detail": "upstream error"})
    )
    client = TrendIntelligenceHttpClient(
        base_url="http://trend.test",
        token="secret",
        timeout_seconds=3,
        max_retries=0,
        retry_backoff_seconds=0,
        client=httpx.Client(transport=transport),
    )

    with pytest.raises(TrendIntelligenceGatewayError) as caught:
        client.request("GET", "/internal/v1/analysis-runs/run-1")
    assert caught.value.code == code


def test_enabled_integration_requires_token_and_test_adapter_is_test_only() -> None:
    with pytest.raises(ValueError, match="internal token"):
        Settings(TREND_INTELLIGENCE_ENABLED=True, TREND_INTELLIGENCE_INTERNAL_TOKEN=None)


def test_position_skill_trend_http_contract_uses_versioned_input_and_result_endpoint() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"data": {
                "id": "skill-run-1", "status": "pending", "input_fingerprint": "fp-1",
            }})
        if request.url.path.endswith("/skill-trends"):
            return httpx.Response(200, json={"data": {
                "position_id": "position-1", "graph_version_id": "graph-1",
                "skill_trends": [], "unresolved_terms": [],
            }})
        return httpx.Response(200, json={"data": {
            "id": "skill-run-1", "status": "running", "input_fingerprint": "fp-1",
        }})

    gateway = HttpTrendIntelligenceGatewayV1(TrendIntelligenceHttpClient(
        base_url="http://trend.test", token="secret", timeout_seconds=3,
        max_retries=0, retry_backoff_seconds=0,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    ))
    run = gateway.create_position_skill_trend(CreatePositionSkillTrendV1(
        "request-1", "idem-1", "position-1", "Java engineer", "graph-1",
        (freeze_json_object({
            "skill_id": "skill-java", "skill_name": "Java", "aliases": ["JVM language"],
        }),),
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 2, 1, tzinfo=timezone.utc),
        "catalog-v1", "algorithm-v1", "formula-v1", "config-v1",
    ))
    assert run.run_id == "skill-run-1"
    body = requests[0].read().decode()
    assert '"run_type":"position_skill_trend"' in body
    assert '"skill_id":"skill-java"' in body
    assert gateway.get_position_skill_trend_run(run.run_id).status == "running"
    result = gateway.get_position_skill_trend_result(run.run_id)
    assert result.payload["graph_version_id"] == "graph-1"
    assert requests[-1].url.path.endswith("/analysis-runs/skill-run-1/skill-trends")
