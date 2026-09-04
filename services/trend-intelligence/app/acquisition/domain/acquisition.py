from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Mapping


class CrawlJobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BundleStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    IMPORTED = "imported"
    FAILED = "failed"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"


@dataclass(frozen=True)
class AcquisitionSource:
    id: str
    name: str
    source_type: str
    endpoint_config: Mapping[str, object]
    auth_config: Mapping[str, object]
    rate_limit_rps: float
    compliance_policy: Mapping[str, object]
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class CrawlJob:
    id: str
    source_id: str
    status: CrawlJobStatus
    window_start: datetime
    window_end: datetime
    retry_count: int
    max_retries: int
    rate_limit_rps: float | None
    error_message: str | None
    fetched_count: int
    new_snapshot_count: int
    duplicate_count: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True)
class RawRecord:
    external_id: str
    raw_content: Mapping[str, object]
    content_type: str = "json"
    captured_at: datetime | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RawSnapshot:
    id: str
    job_id: str
    source_id: str
    external_id: str
    raw_content: Mapping[str, object]
    source_version: str
    content_type: str
    captured_at: datetime
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RawSnapshotObservation:
    job_id: str
    snapshot_id: str
    observed_at: datetime


@dataclass(frozen=True)
class Bundle:
    id: str
    job_id: str
    source_id: str
    bundle_type: str
    snapshot_ids: tuple[str, ...]
    payload: Mapping[str, object]
    record_count: int
    source_version: str
    window_start: datetime
    window_end: datetime
    status: BundleStatus
    created_at: datetime


@dataclass(frozen=True)
class OutboxEntry:
    id: str
    aggregate_type: str
    aggregate_id: str
    event_type: str
    payload: Mapping[str, object]
    status: OutboxStatus
    created_at: datetime
    processed_at: datetime | None = None
