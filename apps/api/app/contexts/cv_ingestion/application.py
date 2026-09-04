from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Callable, Protocol
from uuid import uuid4

from app.contexts.cv_ingestion.domain import (
    CVConfirmationResult,
    CVFileInputError,
    CVReviewConfirmation,
    CVReviewResult,
    CVExtractionTaskRecord,
    SourceCVImportResult,
    ValidatedCVSnapshotRecord,
)
from app.contexts.cv_ingestion.review import (
    apply_field_decisions,
    validate_confirmed_evidence,
)
from app.contexts.data_validation import CVValidatorSet
from app.contexts.cv_ingestion.ports import (
    CVExtractionProvider,
    CVFileInputPort,
    CVIngestionUnitOfWork,
    ValidatedResumeImporter,
)
from app.domain.accounts import AccountActor
from app.domain.json_types import freeze_json_object, thaw_json_object
from app.domain.resumes import require_personal_role


class CVExtractionNotFound(LookupError):
    pass


class CVExtractionConflict(RuntimeError):
    pass


class CVExtractionBlocked(RuntimeError):
    pass


class CVReviewConflict(RuntimeError):
    pass


class CVSnapshotNotFound(LookupError):
    pass


class CVExtractionTaskDispatcher(Protocol):
    @property
    def is_running(self) -> bool: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def trigger(self, limit: int) -> int: ...


@dataclass(frozen=True)
class RunPendingCVExtractionTasks:
    dispatcher: CVExtractionTaskDispatcher

    def execute(self, limit: int) -> int:
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        return self.dispatcher.trigger(limit)

    @property
    def is_running(self) -> bool:
        return self.dispatcher.is_running

    def start(self) -> None:
        self.dispatcher.start()

    def stop(self) -> None:
        self.dispatcher.stop()


class CVIngestionUseCases:
    def __init__(
        self,
        uow_factory: Callable[[], CVIngestionUnitOfWork],
        provider: CVExtractionProvider,
        resume_importer: ValidatedResumeImporter,
        validator: CVValidatorSet,
        file_input: CVFileInputPort | None = None,
        *,
        enabled: bool,
        max_attempts: int,
    ) -> None:
        self._uow_factory = uow_factory
        self._provider = provider
        self._resume_importer = resume_importer
        self._enabled = enabled
        self._max_attempts = max_attempts
        self._validation = validator
        self._file_input = file_input

    def import_and_schedule(
        self,
        actor: AccountActor,
        *,
        source_record_id: str,
        raw_text: str,
        source_platform: str = "personal_resume",
        source_version: str = "1",
    ) -> SourceCVImportResult:
        self._require_enabled()
        require_personal_role(actor.role)
        text = raw_text.strip()
        if not text:
            raise ValueError("CV raw_text must not be empty")
        if not source_record_id.strip():
            raise ValueError("source_record_id must not be empty")
        with self._uow_factory() as uow:
            result = uow.repository.import_and_schedule(
                owner_id=actor.account_id,
                source_platform=source_platform,
                source_record_id=source_record_id,
                raw_text=text,
                source_version=source_version,
                request_id=self._provider.request_id,
                max_attempts=self._max_attempts,
            )
            uow.commit()
            return result

    def upload_and_schedule(
        self,
        actor: AccountActor,
        *,
        filename: str,
        content_type: str | None,
        content: bytes,
        use_ocr: bool,
    ) -> SourceCVImportResult:
        self._require_enabled()
        require_personal_role(actor.role)
        if self._file_input is None:
            raise CVExtractionBlocked("CV file input is not configured")
        extraction = self._file_input.extract(
            actor,
            filename=filename,
            content_type=content_type,
            content=content,
            use_ocr=use_ocr,
        )
        if extraction.extraction_status == "failed":
            raise CVFileInputError(
                extraction.error_code or "CV_FILE_TYPE_UNSUPPORTED",
                extraction.error_message or "CV file text extraction failed",
            )
        text = extraction.raw_text.strip()
        if not text:
            raise CVFileInputError("CV_TEXT_EMPTY", "CV file produced no text")
        with self._uow_factory() as uow:
            result = uow.repository.upload_and_schedule(
                owner_id=actor.account_id,
                source_platform="personal_file_upload",
                source_record_id=f"file:{extraction.source_file_id}",
                raw_text=text,
                source_version="1",
                request_id=self._provider.request_id,
                max_attempts=self._max_attempts,
                source_file_id=extraction.source_file_id,
                original_filename=extraction.original_filename,
                content_type=extraction.content_type,
                extraction_method=extraction.extraction_method,
                extraction_provider=extraction.extraction_provider,
                extraction_provider_version=extraction.extraction_provider_version,
                text_extraction_status=extraction.extraction_status,
                page_count=extraction.page_count,
                quality_flags=extraction.quality_flags,
                ocr_layout=extraction.ocr_layout,
            )
            uow.commit()
            return result

    def import_demo_snapshot(
        self,
        actor: AccountActor,
        *,
        dataset_version: str,
    ) -> CVConfirmationResult:
        """Create and confirm a resume from a named bundled snapshot.

        This is an explicit demo path. It never calls the LLM extraction method
        and is not reachable as a fallback from file upload failures.
        """
        self._require_enabled()
        require_personal_role(actor.role)
        load_demo = getattr(self._provider, "load_demo_snapshot", None)
        if not callable(load_demo):
            raise CVExtractionBlocked("CV demo snapshot provider is not configured")
        if not dataset_version.strip():
            raise ValueError("dataset_version must not be empty")
        placeholder_id = f"demo:{dataset_version}"
        raw_text, _ = load_demo(
            dataset_version=dataset_version,
            document_id=placeholder_id,
        )
        with self._uow_factory() as uow:
            imported = uow.repository.import_and_schedule(
                owner_id=actor.account_id,
                source_platform="jobgraph_demo_data",
                source_record_id=placeholder_id,
                raw_text=raw_text,
                source_version=dataset_version,
                request_id=f"demo:{dataset_version}",
                max_attempts=self._max_attempts,
            )
            uow.commit()
        task = self.get(actor, imported.cv_extraction_task_id)
        if task.status != "succeeded":
            worker_id = f"cv-demo-{uuid4()}"
            now = datetime.now(timezone.utc)
            with self._uow_factory() as uow:
                if task.status == "failed":
                    uow.repository.retry(task.task_id, now=now)
                claimed = uow.repository.claim(
                    task.task_id,
                    worker_id=worker_id,
                    now=now,
                    lease_expires_at=now + timedelta(minutes=10),
                )
                if claimed is None:
                    raise CVExtractionConflict("CV demo snapshot task could not be claimed")
                uow.commit()
            _, payload = load_demo(
                dataset_version=dataset_version,
                document_id=imported.source_cv_version_id,
            )
            task = self._complete_claimed_payload(
                task.task_id,
                worker_id=worker_id,
                payload=payload,
                source_cv_version_id=imported.source_cv_version_id,
            )
        if task.validation_conclusion == "block" or task.review_id is None:
            raise CVReviewConflict("Demo CV snapshot failed validation")
        return self.confirm(
            actor,
            task.task_id,
            CVReviewConfirmation(
                expected_review_id=task.review_id,
                idempotency_key=f"demo:{dataset_version}:{task.task_id}",
                field_decisions=(),
                display_name="示例简历",
            ),
        )

    def run(self, actor: AccountActor, task_id: str) -> CVExtractionTaskRecord:
        """Compatibility adapter for explicit non-HTTP execution."""
        self._require_enabled()
        require_personal_role(actor.role)
        worker_id = f"cv-compat-{uuid4()}"
        now = datetime.now(timezone.utc)
        with self._uow_factory() as uow:
            task = self._required_task(uow, task_id, actor)
            if task.status == "succeeded":
                return task
            if task.status == "running":
                raise CVExtractionConflict("CV extraction task is already running")
            if task.attempt_count >= task.max_attempts:
                raise CVExtractionConflict("CV extraction task exhausted max attempts")
            if task.status == "failed":
                uow.repository.retry(task_id, now=now)
            claimed = uow.repository.claim(
                task_id,
                worker_id=worker_id,
                now=now,
                lease_expires_at=now + timedelta(minutes=10),
            )
            if claimed is None:
                raise CVExtractionConflict("CV extraction task could not be claimed")
            uow.commit()
        return self.execute_claimed(task_id, worker_id=worker_id)

    def claim_next(self, worker_id: str, lease_seconds: float) -> CVExtractionTaskRecord | None:
        self._require_enabled()
        now = datetime.now(timezone.utc)
        with self._uow_factory() as uow:
            task = uow.repository.claim_next(
                worker_id=worker_id,
                now=now,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
            )
            uow.commit()
            return task

    def heartbeat(self, task_id: str, worker_id: str, lease_seconds: float) -> None:
        now = datetime.now(timezone.utc)
        with self._uow_factory() as uow:
            changed = uow.repository.heartbeat(
                task_id,
                worker_id=worker_id,
                now=now,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
            )
            if not changed:
                raise CVExtractionConflict("CV extraction lease is no longer owned")
            uow.commit()

    def recover_stale(self) -> int:
        with self._uow_factory() as uow:
            count = uow.repository.recover_stale(now=datetime.now(timezone.utc))
            uow.commit()
            return count

    def retry(self, actor: AccountActor, task_id: str) -> CVExtractionTaskRecord:
        self._require_enabled()
        require_personal_role(actor.role)
        with self._uow_factory() as uow:
            self._required_task(uow, task_id, actor)
            try:
                task = uow.repository.retry(task_id, now=datetime.now(timezone.utc))
            except ValueError as exc:
                raise CVExtractionConflict(str(exc)) from exc
            uow.commit()
            return task

    def cancel(self, actor: AccountActor, task_id: str) -> CVExtractionTaskRecord:
        self._require_enabled()
        require_personal_role(actor.role)
        with self._uow_factory() as uow:
            self._required_task(uow, task_id, actor)
            try:
                task = uow.repository.cancel(task_id, now=datetime.now(timezone.utc))
            except ValueError as exc:
                raise CVExtractionConflict(str(exc)) from exc
            uow.commit()
            return task

    def reextract(self, actor: AccountActor, task_id: str) -> CVExtractionTaskRecord:
        """Queue a fresh extraction for the same immutable SourceCVVersion.

        A new task keeps the prior raw result, review, and confirmed snapshot
        addressable for audit and comparison. It is intentionally distinct
        from retry, which only resumes a retryable failed task.
        """
        self._require_enabled()
        require_personal_role(actor.role)
        with self._uow_factory() as uow:
            previous = self._required_task(uow, task_id, actor)
            if previous.status in {"pending", "running"}:
                raise CVExtractionConflict("CV extraction task is still active")
            request_prefix = self._provider.request_id[:75]
            fresh = uow.repository.schedule_reextraction(
                previous.source_cv_version_id,
                request_id=f"{request_prefix}:reextract:{uuid4()}",
                max_attempts=self._max_attempts,
            )
            uow.commit()
            return fresh

    def retry_for_operations(
        self, actor: AccountActor, task_id: str
    ) -> CVExtractionTaskRecord:
        """Queue a retry without impersonating the personal resume owner.

        The task lookup resolves the authoritative owner lineage, while the
        response remains task-only and never exposes CV text or parse payloads.
        """
        self._require_enabled()
        from app.domain.permissions import require_permission

        require_permission(actor.role, "integration.cv.retry")
        with self._uow_factory() as uow:
            task = uow.repository.get_task(task_id)
            if task is None:
                raise CVExtractionNotFound("CV extraction task not found")
            if task.status != "failed":
                raise CVExtractionConflict("Only failed CV extraction tasks can be retried")
            if not task.retryable or task.attempt_count >= task.max_attempts:
                raise CVExtractionConflict("CV extraction task exhausted max attempts")
            try:
                retried = uow.repository.retry(
                    task_id, now=datetime.now(timezone.utc)
                )
            except ValueError as exc:
                raise CVExtractionConflict(str(exc)) from exc
            uow.commit()
            return retried

    def execute_claimed(self, task_id: str, *, worker_id: str) -> CVExtractionTaskRecord:
        with self._uow_factory() as uow:
            task = uow.repository.get_task(task_id)
            if task is None:
                raise CVExtractionNotFound("CV extraction task not found")
            if task.status != "running" or task.claimed_by != worker_id:
                raise CVExtractionConflict("CV extraction task lease is not owned")
            version = uow.repository.get_version(task.source_cv_version_id)
            if version is None:
                raise CVExtractionNotFound("SourceCVVersion not found")
        try:
            last_progress: dict | None = None

            def record_progress(value: dict) -> None:
                nonlocal last_progress
                if value == last_progress:
                    return
                frozen = freeze_json_object(value, field="cv_extraction_progress")
                with self._uow_factory() as progress_uow:
                    progress_uow.repository.record_extraction_progress(
                        task_id,
                        worker_id=worker_id,
                        progress=frozen,
                    )
                    progress_uow.commit()
                last_progress = dict(value)

            payload = self._provider.extract(
                document_id=version.version_id,
                raw_text=version.raw_text,
                progress_callback=record_progress,
            )
            return self._complete_claimed_payload(
                task_id,
                worker_id=worker_id,
                payload=payload,
                source_cv_version_id=version.version_id,
            )
        except Exception as exc:
            with self._uow_factory() as uow:
                current = uow.repository.get_task(task_id)
                if current is not None and current.status == "succeeded":
                    return current
                if (
                    current is not None
                    and current.status == "running"
                    and current.claimed_by == worker_id
                ):
                    uow.repository.mark_failed(
                        task_id,
                        worker_id=worker_id,
                        code=getattr(exc, "code", exc.__class__.__name__),
                        message=str(exc)[:512],
                    )
                    uow.commit()
            raise

    def _complete_claimed_payload(
        self,
        task_id: str,
        *,
        worker_id: str,
        payload,
        source_cv_version_id: str,
    ) -> CVExtractionTaskRecord:
        try:
            with self._uow_factory() as uow:
                uow.repository.record_processing_stage(
                    task_id,
                    worker_id=worker_id,
                    stage="contract_validating",
                    now=datetime.now(timezone.utc),
                )
                uow.commit()
            execution = freeze_json_object(
                payload.get("execution", {}),
                field="cv_execution_metadata",
            )
            execution_id = f"{task_id}:execution"
            validation = self._validation.validate(
                payload, source_cv_version_id=source_cv_version_id
            )
            report = validation.report
            conclusion = {
                "allow": "pass",
                "review": "warn",
                "block": "block",
            }[validation.decision]
            with self._uow_factory() as uow:
                uow.repository.record_validation(
                    task_id,
                    worker_id=worker_id,
                    policy_version=self._validation.policy.version,
                    conclusion=conclusion,
                    report=report,
                    execution_metadata=execution,
                    execution_id=execution_id,
                )
                uow.commit()
            review_payload = freeze_json_object(
                {
                    "execution": thaw_json_object(execution),
                    "extraction": (
                        thaw_json_object(validation.extraction)
                        if validation.extraction is not None
                        else None
                    ),
                    "normalized": (
                        thaw_json_object(validation.normalized)
                        if validation.normalized is not None
                        else None
                    ),
                    "review_flags": [
                        thaw_json_object(item) for item in validation.review_flags
                    ],
                },
                field="cv_review_payload",
            )
            review_id = f"{task_id}:review"
            with self._uow_factory() as uow:
                completed = uow.repository.complete_with_review_pending(
                    task_id,
                    worker_id=worker_id,
                    conclusion=conclusion,
                    report=report,
                    review_payload=review_payload,
                    review_id=review_id,
                )
                uow.commit()
                return completed
        except Exception as exc:
            with self._uow_factory() as uow:
                current = uow.repository.get_task(task_id)
                if current is not None and current.status == "succeeded":
                    return current
                if (
                    current is not None
                    and current.status == "running"
                    and current.claimed_by == worker_id
                ):
                    uow.repository.mark_failed(
                        task_id,
                        worker_id=worker_id,
                        code=getattr(exc, "code", exc.__class__.__name__),
                        message=str(exc)[:512],
                    )
                    uow.commit()
            raise

    def get_review(self, actor: AccountActor, task_id: str) -> CVExtractionTaskRecord:
        self._require_enabled()
        require_personal_role(actor.role)
        with self._uow_factory() as uow:
            return self._required_task(uow, task_id, actor)

    def get_review_context(
        self, actor: AccountActor, task_id: str
    ) -> CVReviewResult:
        self._require_enabled()
        require_personal_role(actor.role)
        with self._uow_factory() as uow:
            task = self._required_task(uow, task_id, actor)
            version = uow.repository.get_version(task.source_cv_version_id)
        if version is None:
            raise CVExtractionNotFound("SourceCVVersion not found")
        return CVReviewResult(
            task=task,
            source_cv_id=version.source_cv_id,
            source_cv_version_id=version.version_id,
            source_text=version.raw_text,
            source_file_id=version.source_file_id,
            content_type=version.content_type,
            ocr_layout=version.ocr_layout,
        )

    def pending_review_task_for_resume(
        self, actor: AccountActor, resume_id: str
    ) -> CVExtractionTaskRecord | None:
        """Return the latest unconfirmed review task for the resume's source CV.

        The resume detail view uses this to offer an entry back into a pending
        manual review (for example after a re-extraction) without requiring the
        task id in the URL. Unknown or foreign resumes simply return None.
        """
        self._require_enabled()
        require_personal_role(actor.role)
        with self._uow_factory() as uow:
            task = uow.repository.find_pending_review_task_for_resume(
                resume_id, actor.account_id
            )
            uow.commit()
        return task

    def confirm(
        self,
        actor: AccountActor,
        task_id: str,
        confirmation: CVReviewConfirmation,
        resume_id: str | None = None,
    ) -> CVConfirmationResult:
        self._require_enabled()
        require_personal_role(actor.role)
        with self._uow_factory() as uow:
            task = self._required_task(uow, task_id, actor)
            version = uow.repository.get_version(task.source_cv_version_id)
        if version is None:
            raise CVExtractionNotFound("SourceCVVersion not found")
        if task.status != "succeeded":
            raise CVReviewConflict("Only succeeded CV extraction tasks can be confirmed")
        if task.validation_conclusion == "block":
            raise CVReviewConflict("Blocked CV extraction cannot be confirmed")
        if confirmation.expected_review_id != task.review_id:
            raise CVReviewConflict("Review payload is stale")
        existing = self._confirmed_result(
            actor, task, confirmation, version, resume_id=resume_id
        )
        if existing is not None:
            return existing
        if task.latest_validated_cv_snapshot_id is not None:
            raise CVReviewConflict(
                "Task is already confirmed; use a snapshot revision instead"
            )
        return self._create_snapshot(
            actor,
            task_id=task.task_id,
            version=version,
            task_review=task,
            confirmation=confirmation,
            supersedes_snapshot_id=None,
            event_type="cv_profile_published",
            resume_id=resume_id,
        )

    def create_revision(
        self,
        actor: AccountActor,
        snapshot_id: str,
        confirmation: CVReviewConfirmation,
    ) -> CVConfirmationResult:
        self._require_enabled()
        require_personal_role(actor.role)
        with self._uow_factory() as uow:
            snapshot = uow.repository.get_snapshot(snapshot_id)
            if snapshot is None:
                raise CVSnapshotNotFound("ValidatedCVSnapshot not found")
            task = uow.repository.get_task(snapshot.cv_extraction_task_id)
            if task is None or task.owner_id != actor.account_id:
                raise CVSnapshotNotFound("ValidatedCVSnapshot not found")
            version = uow.repository.get_version(snapshot.source_cv_version_id)
        if version is None:
            raise CVExtractionNotFound("SourceCVVersion not found")
        if confirmation.expected_review_id != snapshot.snapshot_id:
            raise CVReviewConflict("Snapshot revision is stale")
        existing = self._confirmed_result(
            actor, task, confirmation, version
        )
        if existing is not None:
            return existing
        return self._create_snapshot(
            actor,
            task_id=task.task_id,
            version=version,
            task_review=task,
            confirmation=confirmation,
            supersedes_snapshot_id=snapshot.snapshot_id,
            base_snapshot=snapshot,
            event_type="cv_profile_updated",
        )

    def get_snapshot(
        self, actor: AccountActor, snapshot_id: str
    ) -> ValidatedCVSnapshotRecord:
        self._require_enabled()
        require_personal_role(actor.role)
        with self._uow_factory() as uow:
            snapshot = uow.repository.get_snapshot(snapshot_id)
            if snapshot is None:
                raise CVSnapshotNotFound("ValidatedCVSnapshot not found")
            task = uow.repository.get_task(snapshot.cv_extraction_task_id)
            if task is None or task.owner_id != actor.account_id:
                raise CVSnapshotNotFound("ValidatedCVSnapshot not found")
            return snapshot

    def _confirmed_result(
        self,
        actor: AccountActor,
        task: CVExtractionTaskRecord,
        confirmation: CVReviewConfirmation,
        version,
        resume_id: str | None = None,
    ) -> CVConfirmationResult | None:
        if (
            task.confirmation_status != "confirmed"
            or task.latest_validated_cv_snapshot_id is None
        ):
            return None
        payload_id = confirmation.idempotency_key
        if (
            task.confirmation_idempotency_key == confirmation.idempotency_key
            and task.confirmation_idempotency_id == payload_id
        ):
            snapshot_id = task.latest_validated_cv_snapshot_id
            with self._uow_factory() as uow:
                snapshot = uow.repository.get_snapshot(snapshot_id)
            if snapshot is None:
                raise CVSnapshotNotFound("ValidatedCVSnapshot not found")
            resume_id = self._resume_importer.import_snapshot(
                actor,
                validated_cv_snapshot_id=snapshot_id,
                source_cv_version_id=version.version_id,
                raw_text=version.raw_text,
                extraction_payload=snapshot.extraction_payload,
                normalized_payload=snapshot.normalized_payload,
                review_flags=tuple(snapshot.findings_payload.get("review_flags", ())),
                resume_id=resume_id,
            )
            return CVConfirmationResult(
                snapshot.snapshot_id,
                snapshot.snapshot_revision,
                resume_id,
                task.task_id,
                snapshot.supersedes_snapshot_id,
                idempotency_key=confirmation.idempotency_key,
            )
        if task.confirmation_idempotency_key == confirmation.idempotency_key:
            raise CVReviewConflict("Idempotency key was used with a different payload")
        return None

    def _create_snapshot(
        self,
        actor: AccountActor,
        *,
        task_id: str,
        version,
        task_review: CVExtractionTaskRecord,
        confirmation: CVReviewConfirmation,
        supersedes_snapshot_id: str | None,
        base_snapshot=None,
        event_type: str,
        resume_id: str | None = None,
    ) -> CVConfirmationResult:
        if task_review.review_payload is None:
            review = (
                {
                    "extraction": thaw_json_object(base_snapshot.extraction_payload),
                    "normalized": thaw_json_object(base_snapshot.normalized_payload),
                    "review_flags": thaw_json_object(
                        base_snapshot.findings_payload
                    ).get("review_flags", []),
                }
                if base_snapshot is not None
                else None
            )
        else:
            review = thaw_json_object(task_review.review_payload)
        if review is None:
            raise CVReviewConflict("Review payload is missing")
        extraction = review.get("extraction") or {}
        normalized = review.get("normalized") or {}
        confirmed_extraction, confirmed_normalized = apply_field_decisions(
            extraction,
            normalized,
            confirmation.field_decisions,
        )
        validate_confirmed_evidence(confirmed_extraction, version.raw_text)
        idempotency_id = confirmation.idempotency_key
        execution = task_review.execution_metadata or freeze_json_object({})
        execution_values = thaw_json_object(execution)
        normalization_version = (
            confirmation.normalization_version
            or str(execution_values.get("normalization_version", ""))
        )
        taxonomy_version = (
            confirmation.taxonomy_version
            or str(execution_values.get("taxonomy_version", ""))
        )
        confirmed_at = datetime.now(timezone.utc)
        with self._uow_factory() as uow:
            snapshot = uow.repository.prepare_validated_snapshot(
                task_id,
                worker_id="",
                actor_id=actor.account_id,
                policy_version=self._validation.policy.version,
                conclusion="pass" if task_review.validation_conclusion == "pass" else "warn",
                report=task_review.validation_report_payload
                or freeze_json_object({}),
                extraction_payload=freeze_json_object(
                    confirmed_extraction, field="cv_confirmed_extraction"
                ),
                normalized_payload=freeze_json_object(
                    confirmed_normalized, field="cv_confirmed_normalized"
                ),
                findings_payload=freeze_json_object(
                    {
                        "review_flags": review.get("review_flags", []),
                        "field_decisions": [
                            {
                                "field_id": item.field_id,
                                "field_type": item.field_type,
                                "section": item.section,
                                "item_id": item.item_id,
                                "field_path": item.field_path,
                                "decision": item.decision,
                                "corrected_value": item.corrected_value,
                                "correction_reason": item.correction_reason,
                            }
                            for item in confirmation.field_decisions
                        ],
                    },
                    field="cv_findings",
                ),
                execution_metadata=execution,
                source_file_id=version.source_file_id,
                snapshot_revision=uow.repository.next_snapshot_revision(task_id),
                supersedes_snapshot_id=supersedes_snapshot_id,
                extraction_provider=str(execution_values.get("provider")),
                model=str(execution_values.get("model")),
                prompt_version=str(execution_values.get("prompt_version")),
                extraction_schema_version=str(
                    execution_values.get("schema_version")
                ),
                normalization_version=normalization_version,
                taxonomy_version=taxonomy_version,
                field_decisions=tuple(
                    freeze_json_object(
                        {
                            "field_id": item.field_id,
                            "field_type": item.field_type,
                            "section": item.section,
                            "item_id": item.item_id,
                            "field_path": item.field_path,
                            "decision": item.decision,
                            "corrected_value": item.corrected_value,
                            "correction_reason": item.correction_reason,
                        },
                        field="cv_field_decision",
                    )
                    for item in confirmation.field_decisions
                ),
                evidence_payload=freeze_json_object(
                    confirmed_extraction, field="cv_evidence_payload"
                ),
                confirmed_at=confirmed_at,
            )
            uow.repository.complete_confirmation(
                task_id,
                actor_id=actor.account_id,
                snapshot_id=snapshot.snapshot_id,
                idempotency_key=confirmation.idempotency_key,
                idempotency_id=idempotency_id,
                confirmed_at=confirmed_at,
            )
            uow.commit()
        resume_id = self._resume_importer.import_snapshot(
            actor,
            validated_cv_snapshot_id=snapshot.snapshot_id,
            source_cv_version_id=version.version_id,
            raw_text=version.raw_text,
            extraction_payload=snapshot.extraction_payload,
            normalized_payload=snapshot.normalized_payload,
            review_flags=tuple(snapshot.findings_payload.get("review_flags", ())),
            resume_id=resume_id,
        )
        self._resume_importer.publish_profile_refresh(
            resume_id,
            event_type,
            snapshot_id=snapshot.snapshot_id,
            snapshot_revision=snapshot.snapshot_revision,
            source_version=version.version_id,
        )
        return CVConfirmationResult(
            snapshot.snapshot_id,
            snapshot.snapshot_revision,
            resume_id,
            task_id,
            snapshot.supersedes_snapshot_id,
            idempotency_key=confirmation.idempotency_key,
        )


    def get(self, actor: AccountActor, task_id: str) -> CVExtractionTaskRecord:
        require_personal_role(actor.role)
        with self._uow_factory() as uow:
            return self._required_task(uow, task_id, actor)

    def _required_task(self, uow, task_id: str, actor: AccountActor):
        task = uow.repository.get_task(task_id)
        if task is None or task.owner_id != actor.account_id:
            raise CVExtractionNotFound("CV extraction task not found")
        return task

    def _require_enabled(self) -> None:
        if not self._enabled:
            raise CVExtractionConflict("CV extraction is disabled")
