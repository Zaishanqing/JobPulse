from __future__ import annotations

from datetime import datetime
from collections.abc import Callable
from typing import Protocol

from app.contexts.cv_ingestion.domain import (
    CVDocumentTextExtraction,
    CVExtractionTaskRecord,
    SourceCVImportResult,
    SourceCVVersionRecord,
    ValidatedCVSnapshotRecord,
)
from app.domain.accounts import AccountActor
from app.domain.json_types import FrozenJsonObject


class CVIngestionRepository(Protocol):
    def import_and_schedule(
        self,
        *,
        owner_id: str,
        source_platform: str,
        source_record_id: str,
        raw_text: str,
        source_version: str,
        request_id: str,
        max_attempts: int,
    ) -> SourceCVImportResult: ...

    def upload_and_schedule(
        self,
        *,
        owner_id: str,
        source_platform: str,
        source_record_id: str,
        raw_text: str,
        source_version: str,
        request_id: str,
        max_attempts: int,
        source_file_id: str,
        original_filename: str,
        content_type: str,
        extraction_method: str,
        extraction_provider: str,
        extraction_provider_version: str | None,
        text_extraction_status: str,
        page_count: int,
        quality_flags: tuple[str, ...],
        ocr_layout: tuple[FrozenJsonObject, ...] | None = None,
    ) -> SourceCVImportResult: ...

    def get_task(self, task_id: str) -> CVExtractionTaskRecord | None: ...
    def get_version(self, version_id: str) -> SourceCVVersionRecord | None: ...
    def schedule_reextraction(
        self,
        source_cv_version_id: str,
        *,
        request_id: str,
        max_attempts: int,
    ) -> CVExtractionTaskRecord: ...
    def claim(
        self,
        task_id: str,
        *,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> CVExtractionTaskRecord | None: ...
    def claim_next(
        self, *, worker_id: str, now: datetime, lease_expires_at: datetime
    ) -> CVExtractionTaskRecord | None: ...
    def heartbeat(
        self,
        task_id: str,
        *,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool: ...
    def recover_stale(self, *, now: datetime) -> int: ...
    def retry(self, task_id: str, *, now: datetime) -> CVExtractionTaskRecord: ...
    def cancel(self, task_id: str, *, now: datetime) -> CVExtractionTaskRecord: ...
    def record_processing_stage(
        self,
        task_id: str,
        *,
        worker_id: str,
        stage: str,
        now: datetime,
    ) -> CVExtractionTaskRecord: ...
    def record_extraction_progress(
        self,
        task_id: str,
        *,
        worker_id: str,
        progress: FrozenJsonObject,
    ) -> CVExtractionTaskRecord: ...
    def record_validation(
        self,
        task_id: str,
        *,
        worker_id: str,
        policy_version: str,
        conclusion: str,
        report: FrozenJsonObject,
        execution_metadata: FrozenJsonObject,
        execution_id: str,
    ) -> tuple[str, str]: ...
    def mark_failed(
        self, task_id: str, *, worker_id: str, code: str, message: str
    ) -> CVExtractionTaskRecord: ...
    def complete_without_snapshot(
        self, task_id: str, *, worker_id: str, conclusion: str, report: FrozenJsonObject
    ) -> CVExtractionTaskRecord: ...
    def complete_with_review_pending(
        self,
        task_id: str,
        *,
        worker_id: str,
        conclusion: str,
        report: FrozenJsonObject,
        review_payload: FrozenJsonObject,
        review_id: str,
    ) -> CVExtractionTaskRecord: ...
    def prepare_validated_snapshot(
        self,
        task_id: str,
        *,
        worker_id: str,
        actor_id: str | None,
        policy_version: str,
        conclusion: str,
        report: FrozenJsonObject,
        extraction_payload: FrozenJsonObject,
        normalized_payload: FrozenJsonObject,
        findings_payload: FrozenJsonObject,
        execution_metadata: FrozenJsonObject,
        source_file_id: str | None,
        snapshot_revision: int,
        supersedes_snapshot_id: str | None,
        extraction_provider: str | None,
        model: str | None,
        prompt_version: str | None,
        extraction_schema_version: str | None,
        normalization_version: str | None,
        taxonomy_version: str | None,
        field_decisions: tuple[FrozenJsonObject, ...],
        evidence_payload: FrozenJsonObject,
        confirmed_at,
    ) -> ValidatedCVSnapshotRecord: ...
    def complete_confirmation(
        self,
        task_id: str,
        *,
        actor_id: str,
        snapshot_id: str,
        idempotency_key: str,
        idempotency_id: str,
        confirmed_at,
    ) -> CVExtractionTaskRecord: ...
    def get_snapshot(self, snapshot_id: str) -> ValidatedCVSnapshotRecord | None: ...
    def next_snapshot_revision(self, task_id: str) -> int: ...
    def complete_with_resume(
        self, task_id: str, *, worker_id: str, resume_id: str
    ) -> CVExtractionTaskRecord: ...
    def get_snapshot_by_task(self, task_id: str) -> ValidatedCVSnapshotRecord | None: ...
    def find_pending_review_task_for_resume(
        self, resume_id: str, owner_id: str
    ) -> CVExtractionTaskRecord | None: ...


class CVIngestionUnitOfWork(Protocol):
    repository: CVIngestionRepository
    def __enter__(self) -> "CVIngestionUnitOfWork": ...
    def __exit__(self, exc_type, exc, traceback) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class CVExtractionProvider(Protocol):
    @property
    def request_id(self) -> str: ...
    def extract(
        self,
        *,
        document_id: str,
        raw_text: str,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> FrozenJsonObject: ...


class CVFileInputPort(Protocol):
    def extract(
        self,
        actor: AccountActor,
        *,
        filename: str,
        content_type: str | None,
        content: bytes,
        use_ocr: bool,
    ) -> CVDocumentTextExtraction: ...


class ValidatedResumeImporter(Protocol):
    def import_snapshot(
        self,
        actor: AccountActor,
        *,
        validated_cv_snapshot_id: str,
        source_cv_version_id: str,
        raw_text: str,
        extraction_payload: FrozenJsonObject,
        normalized_payload: FrozenJsonObject,
        review_flags: tuple[FrozenJsonObject, ...],
    ) -> str: ...
    def publish_profile_refresh(
        self,
        resume_id: str,
        event_type: str,
        *,
        snapshot_id: str | None = None,
        snapshot_revision: int | None = None,
        source_version: str | None = None,
    ) -> None: ...
