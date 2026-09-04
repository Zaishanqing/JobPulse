from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable, Mapping

import httpx

from app.contexts.market_intelligence.ports import (
    CreatePositionSkillTrendV1,
    PositionSkillTrendResultV1,
    PositionSkillTrendRunV1,
    CreateMarketPredictionV1,
    TrendIntelligenceGatewayError,
    TrendIntelligenceRunV1,
    TrendPredictionV1,
    TrendSignalV1,
    TrendSourceReportV1,
    TrendSourceSnapshotV1,
)
from app.domain.json_types import freeze_json_object
from app.domain.values import thaw


def _datetime(value: object) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


class TrendIntelligenceHttpClient:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        timeout_seconds: float,
        max_retries: int,
        retry_backoff_seconds: float,
        client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.client = client or httpx.Client(timeout=timeout_seconds)
        self.sleeper = sleeper

    def request(self, method: str, path: str, *, json: Mapping[str, object] | None = None) -> dict[str, object]:
        headers = {"Authorization": f"Bearer {self.token}"}
        for attempt in range(self.max_retries + 1):
            try:
                response = self.client.request(method, f"{self.base_url}{path}", headers=headers, json=json, timeout=self.timeout_seconds)
            except httpx.TimeoutException as exc:
                if attempt < self.max_retries:
                    self.sleeper(self.retry_backoff_seconds * (attempt + 1))
                    continue
                raise TrendIntelligenceGatewayError("TREND_INTELLIGENCE_TIMEOUT", "trend intelligence request timed out", retryable=True, status_code=504) from exc
            except httpx.RequestError as exc:
                if attempt < self.max_retries:
                    self.sleeper(self.retry_backoff_seconds * (attempt + 1))
                    continue
                raise TrendIntelligenceGatewayError("TREND_INTELLIGENCE_UNAVAILABLE", "trend intelligence service is unavailable", retryable=True, status_code=503) from exc
            if response.status_code >= 500 or response.status_code == 429:
                if attempt < self.max_retries:
                    self.sleeper(self.retry_backoff_seconds * (attempt + 1))
                    continue
            if response.status_code >= 400:
                codes = {401: "TREND_INTELLIGENCE_AUTHENTICATION_FAILED", 404: "TREND_INTELLIGENCE_RUN_NOT_FOUND", 409: "TREND_INTELLIGENCE_IDEMPOTENCY_CONFLICT", 422: "TREND_INTELLIGENCE_INVALID_REQUEST"}
                retryable = response.status_code >= 500 or response.status_code == 429
                try:
                    body = response.json()
                    message = str(body.get("detail") or body.get("message") or "trend intelligence request failed")
                except ValueError:
                    message = "trend intelligence request failed"
                raise TrendIntelligenceGatewayError(codes.get(response.status_code, "TREND_INTELLIGENCE_UPSTREAM_ERROR"), message, retryable=retryable, status_code=502 if response.status_code >= 500 else response.status_code)
            try:
                body = response.json()
            except ValueError as exc:
                raise TrendIntelligenceGatewayError("TREND_INTELLIGENCE_INVALID_RESPONSE", "trend intelligence returned invalid JSON", retryable=True) from exc
            data = body.get("data")
            if not isinstance(data, (dict, list)):
                raise TrendIntelligenceGatewayError("TREND_INTELLIGENCE_INVALID_RESPONSE", "trend intelligence response envelope is invalid", retryable=True)
            return {"items": data} if isinstance(data, list) else data
        raise AssertionError("unreachable")


class HttpTrendIntelligenceGatewayV1:
    provider_name = "trend_intelligence_http"

    def __init__(self, client: TrendIntelligenceHttpClient) -> None:
        self.client = client

    @staticmethod
    def _run(data: Mapping[str, object]) -> TrendIntelligenceRunV1:
        return TrendIntelligenceRunV1(run_id=str(data["id"]), status=str(data["status"]), error_message=str(data["error_message"]) if data.get("error_message") else None)

    def create_market_prediction(self, request: CreateMarketPredictionV1) -> TrendIntelligenceRunV1:
        data = self.client.request("POST", "/internal/v1/analysis-runs", json={"contract_version": "trend-analysis.v2", "request_id": request.request_id, "idempotency_key": request.idempotency_key, "time_window": {"start": request.window_start.isoformat(), "end": request.window_end.isoformat()}, "data_sources": list(request.data_sources), "weights": dict(request.weights), "algorithm_version": request.algorithm_version, "formula_version": request.formula_version})
        return self._run(data)

    def get_run(self, run_id: str) -> TrendIntelligenceRunV1:
        return self._run(self.client.request("GET", f"/internal/v1/analysis-runs/{run_id}"))

    def get_sources(self, run_id: str) -> TrendSourceReportV1:
        data = self.client.request("GET", f"/internal/v1/analysis-runs/{run_id}/sources")
        snapshots = tuple(TrendSourceSnapshotV1(snapshot_id=str(item["snapshot_id"]), source=str(item["source"]), external_id=str(item["external_id"]), source_version=str(item["source_version"]), captured_at=_datetime(item.get("captured_at")), published_at=_datetime(item.get("published_at")), title=str(item.get("title", "")), url=str(item["url"]) if item.get("url") else None, extraction_versions=tuple(str(value) for value in item.get("extraction_versions", [])), metadata=item.get("metadata", {}) if isinstance(item.get("metadata", {}), dict) else {}) for item in data.get("snapshots", []))
        return TrendSourceReportV1(source_coverage=float(data.get("source_coverage", 0)), missing_sources=tuple(str(item) for item in data.get("missing_sources", [])), quality_flags=tuple(str(item) for item in data.get("quality_flags", [])), sources=tuple(item for item in data.get("sources", []) if isinstance(item, dict)), snapshots=snapshots)

    def get_signals(self, run_id: str) -> tuple[TrendSignalV1, ...]:
        data = self.client.request("GET", f"/internal/v1/analysis-runs/{run_id}/signals")["items"]
        return tuple(TrendSignalV1(source=str(item["source"]), industry_domain=str(item["industry_domain"]), signal_strength=float(item["signal_strength"]), raw_value=float(item["raw_value"]), keywords=tuple(str(value) for value in item.get("keywords", [])), evidence_snapshot_ids=tuple(str(value) for value in item.get("evidence_snapshot_ids", []))) for item in data)

    def get_predictions(self, run_id: str) -> tuple[TrendPredictionV1, ...]:
        data = self.client.request("GET", f"/internal/v1/analysis-runs/{run_id}/predictions")["items"]
        values = []
        for item in data:
            window = item.get("time_window", {})
            key_source = f"{item.get('job_name', '')}\0{item.get('industry_domain', '')}"
            values.append(TrendPredictionV1(candidate_key=key_source, job_name=str(item["job_name"]), industry_domain=str(item["industry_domain"]), emergence_score=float(item["emergence_score"]), source_scores=item.get("source_scores", {}) if isinstance(item.get("source_scores", {}), dict) else {}, related_keywords=tuple(str(value) for value in item.get("related_keywords", [])), evidence_snapshot_ids=tuple(str(value) for value in item.get("evidence_snapshot_ids", [])), algorithm_version=str(item["algorithm_version"]), formula_version=str(item["formula_version"]), window_start=_datetime(window.get("start")), window_end=_datetime(window.get("end")), source_coverage=float(item.get("source_coverage", 0)), missing_sources=tuple(str(value) for value in item.get("missing_sources", [])), quality_flags=tuple(str(value) for value in item.get("quality_flags", []))))
        return tuple(values)

    @staticmethod
    def _skill_run(data: Mapping[str, object]) -> PositionSkillTrendRunV1:
        return PositionSkillTrendRunV1(
            str(data["id"]), str(data["status"]),
            str(data["error_message"]) if data.get("error_message") else None,
        )

    def create_position_skill_trend(self, request: CreatePositionSkillTrendV1) -> PositionSkillTrendRunV1:
        data = self.client.request("POST", "/internal/v1/analysis-runs", json={
            "contract_version": "trend-analysis.v2",
            "run_type": "position_skill_trend",
            "request_id": request.request_id,
            "idempotency_key": request.idempotency_key,
            "position_id": request.position_id,
            "position_name": request.position_name,
            "graph_version": request.graph_version_id,
            "standard_skills": [thaw(item) for item in request.standard_skills],
            "time_window": {"start": request.window_start.isoformat(), "end": request.window_end.isoformat()},
            "skill_catalog_version": request.skill_catalog_version,
            "algorithm_version": request.algorithm_version,
            "formula_version": request.formula_version,
            "config_version": request.config_version,
            "data_sources": list(request.data_sources),
            "weights": {"academic": 0.25, "policy": 0.25, "funding": 0.25, "github": 0.25},
        })
        return self._skill_run(data)

    def get_position_skill_trend_run(self, run_id: str) -> PositionSkillTrendRunV1:
        return self._skill_run(self.client.request("GET", f"/internal/v1/analysis-runs/{run_id}"))

    def get_position_skill_trend_result(self, run_id: str) -> PositionSkillTrendResultV1:
        data = self.client.request("GET", f"/internal/v1/analysis-runs/{run_id}/skill-trends")
        return PositionSkillTrendResultV1(freeze_json_object(data))

    def create_trend_change_analysis(self, payload: dict[str, object]) -> dict[str, object]:
        return self.client.request("POST", "/internal/v1/trend-change/analyses", json=payload)

    def create_trend_change_from_history(self, payload: dict[str, object]) -> dict[str, object]:
        return self.client.request("POST", "/internal/v1/trend-change/analyses/from-history", json=payload)

    def get_trend_change_analysis(
        self,
        analysis_id: str,
        *,
        subject_id: str | None = None,
        window: str | None = None,
        trend_state: str | None = None,
    ) -> dict[str, object]:
        params = []
        if subject_id:
            params.append(f"subject_id={subject_id}")
        if window:
            params.append(f"window={window}")
        if trend_state:
            params.append(f"trend_state={trend_state}")
        path = f"/internal/v1/trend-change/analyses/{analysis_id}"
        if params:
            path += "?" + "&".join(params)
        return self.client.request("GET", path)

    def get_trend_change_points(
        self,
        analysis_id: str,
        *,
        subject_id: str | None = None,
        window: str | None = None,
        trend_state: str | None = None,
    ) -> dict[str, object]:
        params = []
        if subject_id:
            params.append(f"subject_id={subject_id}")
        if window:
            params.append(f"window={window}")
        if trend_state:
            params.append(f"trend_state={trend_state}")
        path = f"/internal/v1/trend-change/analyses/{analysis_id}/change-points"
        if params:
            path += "?" + "&".join(params)
        return self.client.request("GET", path)


class DisabledTrendIntelligenceGatewayV1:
    provider_name = "trend_intelligence_http"

    @staticmethod
    def _disabled():
        raise TrendIntelligenceGatewayError("TREND_INTELLIGENCE_DISABLED", "trend intelligence integration is disabled", retryable=False, status_code=503)

    def create_market_prediction(self, request: CreateMarketPredictionV1):
        return self._disabled()

    def get_run(self, run_id: str):
        return self._disabled()

    def get_sources(self, run_id: str):
        return self._disabled()

    def get_signals(self, run_id: str):
        return self._disabled()

    def get_predictions(self, run_id: str):
        return self._disabled()

    def create_position_skill_trend(self, request: CreatePositionSkillTrendV1):
        return self._disabled()

    def get_position_skill_trend_run(self, run_id: str):
        return self._disabled()

    def get_position_skill_trend_result(self, run_id: str):
        return self._disabled()

    def create_trend_change_analysis(self, payload: dict[str, object]):
        return self._disabled()

    def create_trend_change_from_history(self, payload: dict[str, object]):
        return self._disabled()

    def get_trend_change_analysis(self, analysis_id: str, *, subject_id=None, window=None, trend_state=None):
        return self._disabled()

    def get_trend_change_points(self, analysis_id: str, *, subject_id=None, window=None, trend_state=None):
        return self._disabled()


class TestTrendIntelligenceGatewayV1:
    """Explicit deterministic adapter available only in the test composition root."""

    provider_name = "trend_intelligence_test"

    def __init__(self) -> None:
        self.skill_requests: dict[str, CreatePositionSkillTrendV1] = {}

    def create_market_prediction(self, request: CreateMarketPredictionV1) -> TrendIntelligenceRunV1:
        return TrendIntelligenceRunV1(f"test-run-{request.idempotency_key}", "succeeded")

    def get_run(self, run_id: str) -> TrendIntelligenceRunV1:
        return TrendIntelligenceRunV1(run_id, "succeeded")

    def get_sources(self, run_id: str) -> TrendSourceReportV1:
        snapshot = TrendSourceSnapshotV1("test-snapshot-1", "policy", "policy-1", "test.v1", datetime.now(timezone.utc), datetime.now(timezone.utc), "人工智能政策", "https://example.test/policy", ("yake.v1",), {"test_adapter": True})
        return TrendSourceReportV1(1.0, (), ("test_adapter",), ({"source": "policy", "status": "succeeded", "records_fetched": 1, "error": None},), (snapshot,))

    def get_signals(self, run_id: str) -> tuple[TrendSignalV1, ...]:
        return (TrendSignalV1("policy", "人工智能", 0.8, 1.0, ("大模型",), ("test-snapshot-1",)),)

    def get_predictions(self, run_id: str) -> tuple[TrendPredictionV1, ...]:
        return (TrendPredictionV1("test-candidate-ai", "AI大模型训练师", "人工智能/互联网", 0.8, {"policy": 0.8, "academic": 0.0, "funding": 0.0, "github": 0.0}, ("大模型",), ("test-snapshot-1",), "test-algorithm-v1", "test-formula-v1", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 3, 1, tzinfo=timezone.utc), 1.0, (), ("test_adapter",)),)

    def create_position_skill_trend(self, request: CreatePositionSkillTrendV1) -> PositionSkillTrendRunV1:
        run_id = f"test-skill-run-{request.idempotency_key}"
        self.skill_requests[run_id] = request
        return PositionSkillTrendRunV1(run_id, "succeeded")

    def get_position_skill_trend_run(self, run_id: str) -> PositionSkillTrendRunV1:
        return PositionSkillTrendRunV1(run_id, "succeeded")

    def get_position_skill_trend_result(self, run_id: str) -> PositionSkillTrendResultV1:
        request = self.skill_requests[run_id]
        trends = []
        for index, skill in enumerate(request.standard_skills):
            name = str(skill["skill_name"])
            direction = (
                "new" if name == "Docker" else
                "rising" if name in {"Java", "Spring Boot"} else "declining"
            )
            trends.append({
                "skill_id": skill["skill_id"], "skill_name": skill["skill_name"],
                "current_window_signal": 3.0 if index == 0 else 1.0,
                "historical_window_signal": 1.0 if index == 0 else 3.0,
                "trend_score": 0.75 if index == 0 else 0.25,
                "growth_rate": 2.0 if index == 0 else -0.666667,
                "trend_direction": direction, "evidence_count": 2,
                "source_coverage": 1.0, "confidence": 0.9,
                "evidence_references": ["test-snapshot-1"], "quality_flags": [],
                "score_explanation": {
                    "trend_score": {
                        "current_window": 3.0 if index == 0 else 1.0,
                        "historical_window": 1.0 if index == 0 else 3.0,
                    },
                    "source_contributions": {"test_adapter": 1.0},
                },
            })
        payload = {
            "position_id": request.position_id, "position_name": request.position_name,
            "graph_version": request.graph_version_id,
            "skill_catalog_version": request.skill_catalog_version,
            "algorithm_version": request.algorithm_version,
            "formula_version": request.formula_version,
            "config_version": request.config_version,
            "time_window": {"start": request.window_start.isoformat(), "end": request.window_end.isoformat()},
            "historical_window": {}, "skill_trends": trends,
            "new_skills": [], "rising_skills": [item for item in trends if item["trend_direction"] == "rising"],
            "declining_skills": [item for item in trends if item["trend_direction"] == "declining"],
            "skill_combo_shifts": [{
                "from_skill_ids": [str(request.standard_skills[0]["skill_id"])],
                "to_skill_ids": [str(item["skill_id"]) for item in request.standard_skills[:2]],
            }] if len(request.standard_skills) >= 2 else [],
            "unresolved_terms": [],
            "source_coverage": 1.0, "missing_sources": [], "quality_flags": ["test_adapter"],
            "evidence_references": ["test-snapshot-1"],
        }
        return PositionSkillTrendResultV1(freeze_json_object(payload))

    def create_trend_change_analysis(self, payload: dict[str, object]) -> dict[str, object]:
        return {"analysis_id": "test-change-analysis", "subjects": []}

    def create_trend_change_from_history(self, payload: dict[str, object]) -> dict[str, object]:
        return {"analysis_id": "test-change-history", "subjects": []}

    def get_trend_change_analysis(self, analysis_id: str, *, subject_id=None, window=None, trend_state=None) -> dict[str, object]:
        return {"analysis_id": analysis_id, "subjects": []}

    def get_trend_change_points(self, analysis_id: str, *, subject_id=None, window=None, trend_state=None) -> dict[str, object]:
        return {"analysis_id": analysis_id, "subjects": []}
