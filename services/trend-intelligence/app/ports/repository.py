from __future__ import annotations

from datetime import datetime, timedelta
from collections.abc import Mapping
from typing import Protocol

from app.domain.analysis_run import AnalysisRun, AnalysisRunLog, NewAnalysisRun


class IdempotencyConflict(Exception):
    code = "IdempotencyConflict"

    def __init__(
        self,
        *,
        identity_keys: list[str],
        existing_run_id: str | None,
        reason: str = "identity key already belongs to a different analysis input",
    ) -> None:
        self.identity_keys = identity_keys
        self.existing_run_id = existing_run_id
        self.reason = reason
        super().__init__(reason)

    def to_detail(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.reason,
            "identity_keys": self.identity_keys,
            "existing_run_id": self.existing_run_id,
        }


class AnalysisRunRepository(Protocol):
    def create_or_get(self, command: NewAnalysisRun, *, max_attempts: int) -> AnalysisRun: ...

    def get(self, run_id: str) -> AnalysisRun | None: ...

    def logs(self, run_id: str) -> list[AnalysisRunLog]: ...

    def cancel(self, run_id: str) -> AnalysisRun | None: ...

    def claim(self, worker_id: str, *, now: datetime, lease: timedelta) -> AnalysisRun | None: ...

    def renew_lease(self, run_id: str, worker_id: str, *, until: datetime) -> bool: ...

    def succeed(
        self,
        run_id: str,
        worker_id: str,
        result_summary: Mapping[str, int] | None = None,
    ) -> bool: ...

    def fail(
        self,
        run_id: str,
        worker_id: str,
        error: str,
        *,
        retry_at: datetime,
    ) -> bool: ...

    def recover_expired(self, *, now: datetime) -> int: ...
