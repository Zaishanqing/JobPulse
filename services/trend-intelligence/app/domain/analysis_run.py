from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Mapping


class AnalysisRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class NewAnalysisRun:
    contract_version: str
    request_id: str
    idempotency_key: str | None
    window_start: datetime
    window_end: datetime
    data_sources: tuple[str, ...]
    weights: Mapping[str, float]
    algorithm_version: str
    formula_version: str
    run_type: str = "market_prediction"
    run_payload: Mapping[str, object] | None = None

@dataclass(frozen=True)
class AnalysisRun:
    id: str
    contract_version: str
    request_id: str
    idempotency_key: str | None
    status: AnalysisRunStatus
    window_start: datetime
    window_end: datetime
    data_sources: tuple[str, ...]
    weights: Mapping[str, float]
    algorithm_version: str
    formula_version: str
    attempt_count: int
    max_attempts: int
    cancel_requested: bool
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    run_type: str = "market_prediction"
    run_payload: Mapping[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["status"] = self.status.value
        value["config_versions"] = dict((self.run_payload or {}).get("config_versions") or {})
        return value


@dataclass(frozen=True)
class AnalysisRunLog:
    id: int
    run_id: str
    level: str
    event: str
    message: str
    details: Mapping[str, object]
    created_at: datetime
