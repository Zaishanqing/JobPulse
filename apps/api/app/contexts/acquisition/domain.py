"""Acquisition bounded-context domain types and state machine.

The acquisition aggregate is owned by the main system.  Crawler tasks are
external references only; they are never treated as main-system aggregates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypeAlias

AcquisitionStatus: TypeAlias = Literal[
    "pending",
    "crawling",
    "exporting",
    "verifying",
    "importing",
    "completed",
    "crawl_failed",
    "export_failed",
    "verify_failed",
    "import_failed",
    "cancelled",
]

RUNNING_STATUSES = frozenset(
    {"pending", "crawling", "exporting", "verifying", "importing"}
)
TERMINAL_STATUSES = frozenset(
    {
        "completed",
        "crawl_failed",
        "export_failed",
        "verify_failed",
        "import_failed",
        "cancelled",
    }
)
FAILURE_STATUSES = frozenset(
    {"crawl_failed", "export_failed", "verify_failed", "import_failed"}
)

TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"crawling", "cancelled"}),
    "crawling": frozenset({"exporting", "crawl_failed", "cancelled"}),
    "exporting": frozenset({"verifying", "export_failed", "cancelled"}),
    "verifying": frozenset({"importing", "verify_failed", "cancelled"}),
    "importing": frozenset({"completed", "import_failed", "cancelled"}),
    "completed": frozenset(),
    "crawl_failed": frozenset({"pending"}),
    "export_failed": frozenset({"pending"}),
    "verify_failed": frozenset({"pending"}),
    "import_failed": frozenset({"pending"}),
    "cancelled": frozenset(),
}

TERMINAL_FAILURE_RETRYABLE = frozenset({"crawl_failed", "export_failed", "verify_failed", "import_failed"})


class AcquisitionTransitionConflict(RuntimeError):
    pass


class AcquisitionRetryRejected(ValueError):
    pass


def require_transition(current: str, target: str) -> None:
    if target not in TRANSITIONS.get(current, frozenset()):
        raise AcquisitionTransitionConflict(
            f"Invalid acquisition transition: {current} -> {target}"
        )


def can_retry(status: str) -> bool:
    return status in TERMINAL_FAILURE_RETRYABLE


@dataclass(frozen=True)
class AcquisitionJobRecord:
    id: str
    requested_by: str | None
    source: str
    keyword: str
    city: str
    pages: int
    status: str
    progress: float
    crawler_task_id: str | None
    bundle_id: str | None
    bundle_file_name: str | None
    bundle_hash: str | None
    discovered_count: int
    exported_count: int
    imported_count: int
    no_op_count: int
    failed_count: int
    import_batch_id: str | None
    error_code: str | None
    error_message: str | None
    retry_of_id: str | None
    attempt: int
    created_at: datetime | None
    updated_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None

    def with_fields(self, **changes: object) -> "AcquisitionJobRecord":
        values = {
            "id": self.id,
            "requested_by": self.requested_by,
            "source": self.source,
            "keyword": self.keyword,
            "city": self.city,
            "pages": self.pages,
            "status": self.status,
            "progress": self.progress,
            "crawler_task_id": self.crawler_task_id,
            "bundle_id": self.bundle_id,
            "bundle_file_name": self.bundle_file_name,
            "bundle_hash": self.bundle_hash,
            "discovered_count": self.discovered_count,
            "exported_count": self.exported_count,
            "imported_count": self.imported_count,
            "no_op_count": self.no_op_count,
            "failed_count": self.failed_count,
            "import_batch_id": self.import_batch_id,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "retry_of_id": self.retry_of_id,
            "attempt": self.attempt,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
        values.update(changes)
        return AcquisitionJobRecord(**values)


@dataclass(frozen=True)
class AcquisitionJobCreate:
    requested_by: str | None
    source: str
    keyword: str
    city: str
    pages: int
    retry_of_id: str | None = None
    attempt: int = 1


__all__ = [
    "AcquisitionJobCreate",
    "AcquisitionJobRecord",
    "AcquisitionRetryRejected",
    "AcquisitionTransitionConflict",
    "AcquisitionStatus",
    "FAILURE_STATUSES",
    "RUNNING_STATUSES",
    "TERMINAL_STATUSES",
    "TRANSITIONS",
    "TERMINAL_FAILURE_RETRYABLE",
    "can_retry",
    "require_transition",
]
