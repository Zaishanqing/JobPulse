from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Protocol

from app.domain.json_types import FrozenJsonObject


@dataclass(frozen=True)
class CreateMarketPredictionV1:
    request_id: str
    idempotency_key: str
    window_start: datetime
    window_end: datetime
    data_sources: tuple[str, ...]
    weights: Mapping[str, float]
    algorithm_version: str
    formula_version: str


@dataclass(frozen=True)
class TrendIntelligenceRunV1:
    run_id: str
    status: str
    error_message: str | None = None


@dataclass(frozen=True)
class TrendSourceSnapshotV1:
    snapshot_id: str
    source: str
    external_id: str
    source_version: str
    captured_at: datetime | None
    published_at: datetime | None
    title: str
    url: str | None
    extraction_versions: tuple[str, ...]
    metadata: FrozenJsonObject


@dataclass(frozen=True)
class TrendSourceReportV1:
    source_coverage: float
    missing_sources: tuple[str, ...]
    quality_flags: tuple[str, ...]
    sources: tuple[FrozenJsonObject, ...]
    snapshots: tuple[TrendSourceSnapshotV1, ...]


@dataclass(frozen=True)
class TrendSignalV1:
    source: str
    industry_domain: str
    signal_strength: float
    raw_value: float
    keywords: tuple[str, ...]
    evidence_snapshot_ids: tuple[str, ...]


@dataclass(frozen=True)
class TrendPredictionV1:
    candidate_key: str
    job_name: str
    industry_domain: str
    emergence_score: float
    source_scores: Mapping[str, float]
    related_keywords: tuple[str, ...]
    evidence_snapshot_ids: tuple[str, ...]
    algorithm_version: str
    formula_version: str
    window_start: datetime
    window_end: datetime
    source_coverage: float
    missing_sources: tuple[str, ...]
    quality_flags: tuple[str, ...]


class TrendIntelligenceGatewayError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool, status_code: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


class TrendIntelligenceGatewayV1(Protocol):
    provider_name: str

    def create_market_prediction(self, request: CreateMarketPredictionV1) -> TrendIntelligenceRunV1: ...

    def get_run(self, run_id: str) -> TrendIntelligenceRunV1: ...

    def get_sources(self, run_id: str) -> TrendSourceReportV1: ...

    def get_signals(self, run_id: str) -> tuple[TrendSignalV1, ...]: ...

    def get_predictions(self, run_id: str) -> tuple[TrendPredictionV1, ...]: ...

    def create_trend_change_analysis(self, payload: FrozenJsonObject) -> FrozenJsonObject: ...

    def create_trend_change_from_history(self, payload: FrozenJsonObject) -> FrozenJsonObject: ...

    def get_trend_change_analysis(self, analysis_id: str, *, subject_id: str | None = None, window: str | None = None, trend_state: str | None = None) -> FrozenJsonObject: ...

    def get_trend_change_points(self, analysis_id: str, *, subject_id: str | None = None, window: str | None = None, trend_state: str | None = None) -> FrozenJsonObject: ...
