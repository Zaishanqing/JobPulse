from __future__ import annotations

from datetime import datetime
from typing import Protocol, Sequence

from app.domain.market import SourceRecord


class SourceGovernanceStore(Protocol):
    def circuit_allows(self, source: str, now: datetime) -> bool: ...
    def record_attempt(self, *, run_id: str, source: str, status: str, duration_ms: float,
                       records: Sequence[SourceRecord], error_type: str | None,
                       window_end: datetime, failure_threshold: int,
                       open_seconds: int) -> None: ...
    def cache_records(self, run_id: str, source: str, records: Sequence[SourceRecord], request_id: str) -> str: ...
    def replay_records(self, run_id: str, source: str) -> list[SourceRecord] | None: ...
    def source_health(self, source: str | None = None) -> list[dict[str, object]]: ...
