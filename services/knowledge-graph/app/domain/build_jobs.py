from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class BuildJobRecord:
    job_id: int
    job_key: str
    position_id: str
    status: str
    command: dict
    attempts: int
    max_attempts: int
    build_run_id: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    available_at: datetime | None = None


class BuildJobTransitionError(RuntimeError):
    pass
