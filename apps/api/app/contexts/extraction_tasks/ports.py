from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1
from jobgraph_contracts.extraction_bundle import ExtractedJDBundleV1

from app.contexts.catalog import SkillRepository
from app.contexts.source_jds.ports import SourceJDRecord, SourceJDVersionRecord
from app.contexts.source_jds.ports import SourceJDRepository
from app.domain.jd_skill_catalog import CatalogIdentity
from app.domain.json_types import FrozenJsonObject, JsonObject


ExtractionMode = Literal["llm", "rule"]


@dataclass(frozen=True)
class ExtractionTaskRecord:
    id: str
    source_jd_version_id: str
    status: str
    extraction_mode: ExtractionMode
    provider: str
    request_id: str
    attempt_count: int
    max_attempts: int
    started_at: datetime | None
    finished_at: datetime | None
    last_error_code: str | None
    last_error_message: str | None
    retryable: bool
    bundle_payload: JsonObject | None
    claimed_by: str | None
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ExtractionDraftRecord:
    jd_id: str
    parse_result_id: str
    review_task_id: str
    source_jd_id: str
    source_jd_version_id: str
    extraction_task_id: str
    document_id: str
    bundle_id: str
    workflow_status: str
    need_review: bool
    created_at: datetime


@dataclass(frozen=True)
class ExtractionDraftCreate:
    jd_id: str
    source_jd_id: str
    source_jd_version_id: str
    extraction_task_id: str
    document_id: str
    bundle_id: str
    source_platform: str
    source_name: str | None
    title: str
    raw_text: str
    cleaned_text: str
    source_url: str | None
    extraction_provider: str
    extraction_payload: JsonObject
    normalization_payload: JsonObject
    position_title: str | None
    responsibilities: tuple[str, ...]
    required_skills: tuple[JsonObject, ...]
    bonus_skills: tuple[JsonObject, ...]
    education: str | None
    experience: str | None
    industry: str | None
    tools: tuple[str, ...]
    business_scenarios: tuple[str, ...]
    execution_metadata: JsonObject | None = None


@dataclass(frozen=True)
class RawJDDraftCreate:
    jd_id: str
    source_jd_id: str
    source_jd_version_id: str
    source_platform: str
    source_name: str
    title: str
    raw_text: str
    source_url: str | None = None


DataValidationMode = Literal["off", "observe", "enforce"]


@dataclass(frozen=True)
class ValidationTaskReference:
    task_id: str
    status: str
    extraction_task_id: str
    source_jd_version_id: str
    bundle_id: str
    policy_binding_version: str
    created: bool


@dataclass(frozen=True)
class ValidationDraftGateState:
    mode: DataValidationMode
    extraction_task_id: str
    source_jd_version_id: str
    task_id: str | None
    task_status: str | None
    conclusion: str | None
    report_id: str | None
    snapshot_id: str | None
    policy_binding_version: str
    bundle_id: str
    snapshot_bundle: FrozenJsonObject | None
    snapshot_extraction_task_id: str | None
    snapshot_source_jd_version_id: str | None
    snapshot_data_validation_task_id: str | None
    snapshot_validation_report_id: str | None
    snapshot_validation_conclusion: str | None
    snapshot_bundle_id: str | None
    decision: str


class ValidationTaskSchedulerPort(Protocol):
    def ensure_for_extraction(
        self,
        *,
        extraction_task_id: str,
        source_jd_version_id: str,
        bundle_payload: JsonObject,
    ) -> ValidationTaskReference: ...


class ValidationDraftGatePort(Protocol):
    def read_for_extraction(
        self,
        *,
        mode: DataValidationMode,
        extraction_task_id: str,
        source_jd_version_id: str,
        bundle_payload: JsonObject | None,
    ) -> ValidationDraftGateState: ...


class ExtractionDraftRepository(Protocol):
    def get_by_task(self, task_id: str) -> ExtractionDraftRecord | None: ...

    def raw_exists(self, source_jd_version_id: str) -> bool: ...

    def list_by_source_version(
        self, source_jd_version_id: str
    ) -> tuple[ExtractionDraftRecord, ...]: ...

    def add(self, draft: ExtractionDraftCreate) -> ExtractionDraftRecord: ...

    def add_raw(self, draft: RawJDDraftCreate) -> str: ...


class JDExtractionProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def enabled(self) -> bool: ...

    @property
    def request_id(self) -> str: ...

    def extract(self, envelope: CrawlerJDEnvelopeV1) -> ExtractedJDBundleV1: ...


class ExtractionProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.retryable = retryable


class ExtractionTaskRepository(Protocol):
    def get(self, task_id: str, *, for_update: bool = False) -> ExtractionTaskRecord | None: ...

    def get_by_idempotency_key(
        self, source_jd_version_id: str, request_id: str
    ) -> ExtractionTaskRecord | None: ...

    def add(
        self,
        source_jd_version_id: str,
        extraction_mode: ExtractionMode,
        provider: str,
        request_id: str,
        max_attempts: int,
    ) -> ExtractionTaskRecord: ...

    def mark_running(self, task_id: str, started_at: datetime) -> ExtractionTaskRecord: ...

    def mark_succeeded(
        self, task_id: str, bundle_payload: JsonObject, finished_at: datetime
    ) -> ExtractionTaskRecord: ...

    def mark_failed(
        self,
        task_id: str,
        error_code: str,
        error_message: str,
        retryable: bool,
        finished_at: datetime,
    ) -> ExtractionTaskRecord: ...

    def list(
        self,
        *,
        status: str | None,
        source_jd_version_id: str | None,
        offset: int,
        limit: int,
    ) -> tuple[tuple[ExtractionTaskRecord, ...], int]: ...

    def claim_next(
        self, worker_id: str, now: datetime, lease_expires_at: datetime
    ) -> ExtractionTaskRecord | None: ...

    def renew_lease(
        self, task_id: str, worker_id: str, now: datetime, lease_expires_at: datetime
    ) -> bool: ...

    def recover_stale(self, now: datetime) -> int: ...


class ExtractionSourceRepository(Protocol):
    def get_source(self, source_jd_id: str) -> SourceJDRecord | None: ...

    def get_version(self, version_id: str) -> SourceJDVersionRecord | None: ...


class ExtractionTaskUnitOfWork(Protocol):
    tasks: ExtractionTaskRepository
    drafts: ExtractionDraftRepository
    sources: ExtractionSourceRepository
    source_jds: SourceJDRepository
    skills: SkillRepository
    validation_tasks: ValidationTaskSchedulerPort
    validation_gate: ValidationDraftGatePort

    def __enter__(self) -> "ExtractionTaskUnitOfWork": ...

    def __exit__(self, exc_type, exc, traceback) -> None: ...

    def acquire_task_lock(self, lock_key: str) -> None: ...

    def acquire_orchestration_lock(self, lock_key: str) -> None: ...

    def catalog_entries(self): ...

    def catalog_identity(self) -> CatalogIdentity: ...

    def position_catalog_identity(self) -> CatalogIdentity: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class ExtractionTaskUoWFactory(Protocol):
    def __call__(self) -> AbstractContextManager[ExtractionTaskUnitOfWork]: ...


class ExtractionTaskDispatcher(Protocol):
    @property
    def is_running(self) -> bool: ...

    def trigger(self, limit: int) -> int: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...
