from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping
from uuid import uuid4

from pydantic import ValidationError

from app.contexts.extraction_tasks.ports import (
    DataValidationMode,
    ExtractionMode,
    ExtractionProviderError,
    ExtractionDraftCreate,
    ExtractionDraftRecord,
    ExtractionTaskRecord,
    ExtractionTaskUoWFactory,
    ExtractionTaskDispatcher,
    JDExtractionProvider,
    RawJDDraftCreate,
)
from app.domain.json_types import freeze_json_object, thaw_json_object
from app.contexts.source_jds.application import (
    import_source_jd_in_uow,
)
from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1
from jobgraph_contracts.extraction_bundle import (
    ExtractedJDBundleV1,
    parse_extracted_jd_bundle,
    validate_bundle_matches_envelope,
)
from app.contexts.extraction_tasks.drafts import map_bundle_to_framework_draft


class ExtractionTaskNotFound(LookupError):
    pass


class ExtractionTaskConflict(RuntimeError):
    pass


class ExtractionTaskRetryRejected(ValueError):
    pass


class ExtractionDraftValidationError(ValueError):
    pass


class ExtractionDraftNotReady(RuntimeError):
    pass


class ExtractionValidationGateError(RuntimeError):
    code = "validation_result_inconsistent"
    safe_message = "Validation result is inconsistent."

    def __init__(self) -> None:
        super().__init__(self.safe_message)


class ExtractionValidationPending(ExtractionValidationGateError):
    code = "validation_pending"
    safe_message = "Validation is still pending."


class ExtractionValidationFailed(ExtractionValidationGateError):
    code = "validation_failed"
    safe_message = "Validation did not complete successfully."


class ExtractionValidationBlocked(ExtractionValidationGateError):
    code = "validation_blocked"
    safe_message = "Validation blocked this draft import."


class ExtractionValidationTaskMissing(ExtractionValidationGateError):
    code = "validation_task_missing"
    safe_message = "The required validation task is missing."


class ExtractionValidationSnapshotMissing(ExtractionValidationGateError):
    code = "validation_snapshot_missing"
    safe_message = "The validated bundle snapshot is missing."


class ExtractionValidationInconsistent(ExtractionValidationGateError):
    def __init__(self, code: str = "validation_result_inconsistent") -> None:
        if code not in {
            "validation_result_inconsistent",
            "validation_snapshot_inconsistent",
        }:
            code = "validation_result_inconsistent"
        self.code = code
        super().__init__()


_VALIDATION_GATE_ERRORS = {
    "validation_pending": ExtractionValidationPending,
    "validation_failed": ExtractionValidationFailed,
    "validation_blocked": ExtractionValidationBlocked,
    "validation_task_missing": ExtractionValidationTaskMissing,
    "validation_snapshot_missing": ExtractionValidationSnapshotMissing,
}


@dataclass(frozen=True)
class ImportAndScheduleResult:
    source_jd_id: str
    source_jd_version_id: str
    extraction_task_id: str
    created_source: bool
    created_version: bool
    created_task: bool
    task_status: str


@dataclass(frozen=True)
class ExtractionTaskPage:
    items: tuple[ExtractionTaskRecord, ...]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class RunPendingExtractionTasks:
    dispatcher: ExtractionTaskDispatcher

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


class ExtractionTaskUseCases:
    def __init__(
        self,
        uow_factory: ExtractionTaskUoWFactory,
        providers: Mapping[ExtractionMode, JDExtractionProvider] | JDExtractionProvider,
        max_attempts: int,
        clock: Callable[[], datetime] | None = None,
        data_validation_mode: DataValidationMode = "off",
    ) -> None:
        if data_validation_mode not in {"off", "observe", "enforce"}:
            raise ValueError("Unsupported data validation mode")
        self._uow_factory = uow_factory
        if isinstance(providers, Mapping):
            self._providers = dict(providers)
        else:
            mode = getattr(providers, "mode", "llm")
            self._providers = {mode: providers}
        self._max_attempts = max_attempts
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._data_validation_mode = data_validation_mode

    def create_extraction_task(
        self, source_jd_version_id: str, extraction_mode: ExtractionMode
    ) -> ExtractionTaskRecord:
        provider = self._provider_for_mode(extraction_mode)
        request_id = provider.request_id
        with self._uow_factory() as uow:
            uow.acquire_task_lock(f"create:{source_jd_version_id}:{request_id}")
            if uow.sources.get_version(source_jd_version_id) is None:
                raise ExtractionTaskNotFound("SourceJDVersion not found")
            task, _ = self._create_task_in_uow(uow, source_jd_version_id, extraction_mode)
            uow.commit()
            return task

    def import_crawler_envelope_as_jd(
        self, envelope: CrawlerJDEnvelopeV1, extraction_mode: ExtractionMode
    ) -> ImportAndScheduleResult:
        """Import a crawled JD as a raw pending JD, like a manual JD import.

        No background rule/LLM extraction and no Validation draft gate: the
        JD appears in the JD data center with parse_status=pending and can be
        parsed later with the JD data center parse action.
        """
        if not isinstance(envelope, CrawlerJDEnvelopeV1):
            raise TypeError("envelope must be CrawlerJDEnvelopeV1")
        lock_key = f"import-raw-jd:{envelope.source_platform}:{envelope.source_record_id}"
        with self._uow_factory() as uow:
            uow.acquire_orchestration_lock(lock_key)
            imported = import_source_jd_in_uow(envelope, uow)
            if not uow.drafts.raw_exists(imported.source_jd_version_id):
                uow.drafts.add_raw(
                    RawJDDraftCreate(
                        jd_id=str(uuid4()),
                        source_jd_id=imported.source_jd_id,
                        source_jd_version_id=imported.source_jd_version_id,
                        source_platform=envelope.source_platform,
                        source_name=(
                            envelope.company_name_raw or envelope.source_platform
                        ),
                        title=envelope.job_title_raw or "未命名岗位",
                        raw_text=envelope.raw_text,
                        source_url=envelope.source_url,
                    )
                )
            uow.commit()
            return ImportAndScheduleResult(
                source_jd_id=imported.source_jd_id,
                source_jd_version_id=imported.source_jd_version_id,
                extraction_task_id=None,
                created_source=imported.created_source,
                created_version=imported.created_version,
                created_task=False,
                task_status="skipped",
            )

    def list_extraction_tasks(
        self,
        *,
        status: str | None = None,
        source_jd_version_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> ExtractionTaskPage:
        if status is not None and status not in {"pending", "running", "succeeded", "failed"}:
            raise ValueError("Invalid extraction task status")
        if page < 1 or page_size < 1 or page_size > 100:
            raise ValueError("Invalid pagination")
        with self._uow_factory() as uow:
            items, total = uow.tasks.list(
                status=status,
                source_jd_version_id=source_jd_version_id,
                offset=(page - 1) * page_size,
                limit=page_size,
            )
            return ExtractionTaskPage(items, total, page, page_size)

    def get_extraction_task(self, task_id: str) -> ExtractionTaskRecord:
        with self._uow_factory() as uow:
            task = uow.tasks.get(task_id)
            if task is None:
                raise ExtractionTaskNotFound("ExtractionTask not found")
            return task

    def import_extraction_bundle(
        self,
        task_id: str,
        *,
        position_bindings: Mapping[str, tuple[str, str]] | None = None,
    ) -> ExtractionDraftRecord:
        """Import one persisted successful Bundle into the existing JD draft layer."""
        existing = self._get_existing_draft(task_id)
        if existing is not None:
            return existing
        if self._data_validation_mode == "enforce":
            gate = self._read_validation_gate(task_id)
            if gate.decision != "allow":
                self._ensure_current_validation_task(task_id)
        elif self._data_validation_mode == "observe":
            self._ensure_current_validation_task(task_id)
        with self._uow_factory() as uow:
            uow.acquire_task_lock(f"import-draft:{task_id}")
            task = uow.tasks.get(task_id, for_update=True)
            if task is None:
                raise ExtractionTaskNotFound("ExtractionTask not found")
            if task.status != "succeeded":
                raise ExtractionDraftNotReady("Only succeeded ExtractionTask can be imported")
            if task.bundle_payload is None and self._data_validation_mode != "enforce":
                raise ExtractionDraftValidationError(
                    "Succeeded ExtractionTask has no persisted Bundle"
                )
            version = uow.sources.get_version(task.source_jd_version_id)
            if version is None:
                raise ExtractionTaskNotFound("SourceJDVersion not found")
            source = uow.sources.get_source(version.source_jd_id)
            if source is None:
                raise ExtractionTaskNotFound("SourceJD not found")
            existing = uow.drafts.get_by_task(task_id)
            if existing is not None:
                return existing
            try:
                envelope = self._envelope(source, version)
                bundle_payload = task.bundle_payload
                if self._data_validation_mode == "enforce":
                    gate = uow.validation_gate.read_for_extraction(
                        mode=self._data_validation_mode,
                        extraction_task_id=task.id,
                        source_jd_version_id=task.source_jd_version_id,
                        bundle_payload=task.bundle_payload,
                    )
                    self._enforce_validation_gate(gate)
                    if gate.snapshot_bundle is None:
                        raise ExtractionValidationSnapshotMissing()
                    bundle_payload = gate.snapshot_bundle
                raw_bundle = thaw_json_object(bundle_payload)
                if position_bindings is not None:
                    classification = raw_bundle.get("normalized_result", {}).get(
                        "job_classification"
                    )
                    if not isinstance(classification, dict):
                        raise ValueError("JD classification is missing")
                    if classification.get("classification_status") not in {
                        "resolved",
                        "manually_confirmed",
                    }:
                        raise ValueError("JD classification is not resolved")
                    source_position_code = str(classification.get("position_code") or "").strip()
                    source_name = str(classification.get("position_name") or "").strip()
                    binding = position_bindings.get(source_position_code)
                    if binding is None:
                        raise ValueError(
                            "JD classification has no standard position binding: "
                            f"{source_position_code}"
                        )
                    if not source_name or source_name != binding[1]:
                        raise ValueError(
                            f"JD classification conflicts with standard position: {source_position_code}"
                        )
                    classification["position_id"] = binding[0]
                bundle = parse_extracted_jd_bundle(raw_bundle)
                validate_bundle_matches_envelope(envelope, bundle)
                jd_id = str(uuid4())
                catalog_skills, catalog_aliases = uow.catalog_entries()
                skill_identity = uow.catalog_identity()
                position_identity = uow.position_catalog_identity()
                material = map_bundle_to_framework_draft(
                    bundle,
                    framework_jd_id=jd_id,
                    raw_text=bundle.cleaned_text,
                    fallback_title=version.job_title_raw,
                    catalog_skills=catalog_skills,
                    catalog_aliases=catalog_aliases,
                )
            except (ValidationError, ValueError, TypeError) as exc:
                if self._data_validation_mode == "enforce":
                    raise ExtractionDraftValidationError(
                        "Validated Bundle Snapshot cannot be imported."
                    ) from exc
                raise ExtractionDraftValidationError(
                    f"Extraction Bundle cannot be imported: {exc}"
                ) from exc
            result = uow.drafts.add(
                ExtractionDraftCreate(
                    jd_id=jd_id,
                    source_jd_id=source.id,
                    source_jd_version_id=version.id,
                    extraction_task_id=task.id,
                    document_id=bundle.extraction_result.document_id,
                    bundle_id=bundle.schema_version,
                    source_platform=source.source_platform,
                    source_name=version.company_name_raw,
                    title=material.title,
                    raw_text=version.raw_text,
                    cleaned_text=bundle.cleaned_text,
                    source_url=version.source_url,
                    extraction_provider=bundle.extraction_provider,
                    extraction_payload=material.extraction_payload,
                    normalization_payload=material.normalization_payload,
                    position_title=material.position_title,
                    responsibilities=material.responsibilities,
                    required_skills=material.required_skills,
                    bonus_skills=material.bonus_skills,
                    education=material.education,
                    experience=material.experience,
                    industry=material.industry,
                    tools=material.tools,
                    business_scenarios=material.business_scenarios,
                    execution_metadata=freeze_json_object(
                        {
                            "catalog_identity": {
                                "skill": {
                                    "catalog_version": (
                                        skill_identity.catalog_version
                                    ),
                                    "content_hash": skill_identity.content_hash,
                                },
                                "position": {
                                    "catalog_version": (
                                        position_identity.catalog_version
                                    ),
                                    "content_hash": (
                                        position_identity.content_hash
                                    ),
                                },
                            }
                        }
                    ),
                )
            )
            uow.commit()
            return result

    def _read_validation_gate(self, task_id: str):
        with self._uow_factory() as uow:
            task = uow.tasks.get(task_id)
            if task is None:
                raise ExtractionTaskNotFound("ExtractionTask not found")
            if task.status != "succeeded":
                raise ExtractionDraftNotReady("Only succeeded ExtractionTask can be imported")
            return uow.validation_gate.read_for_extraction(
                mode=self._data_validation_mode,
                extraction_task_id=task.id,
                source_jd_version_id=task.source_jd_version_id,
                bundle_payload=task.bundle_payload,
            )

    def _get_existing_draft(self, task_id: str) -> ExtractionDraftRecord | None:
        with self._uow_factory() as uow:
            task = uow.tasks.get(task_id)
            if task is None:
                raise ExtractionTaskNotFound("ExtractionTask not found")
            return uow.drafts.get_by_task(task_id)

    def _ensure_current_validation_task(self, task_id: str) -> ExtractionTaskRecord:
        with self._uow_factory() as uow:
            uow.acquire_task_lock(f"ensure-validation:{task_id}")
            task = uow.tasks.get(task_id, for_update=True)
            if task is None:
                raise ExtractionTaskNotFound("ExtractionTask not found")
            if task.status != "succeeded":
                raise ExtractionDraftNotReady("Only succeeded ExtractionTask can be validated")
            if task.bundle_payload is None:
                raise ExtractionDraftValidationError(
                    "Succeeded ExtractionTask has no persisted Bundle"
                )
            uow.validation_tasks.ensure_for_extraction(
                extraction_task_id=task.id,
                source_jd_version_id=task.source_jd_version_id,
                bundle_payload=task.bundle_payload,
            )
            uow.commit()
            return task

    @staticmethod
    def _enforce_validation_gate(gate) -> None:
        if gate.decision == "allow":
            if (
                gate.task_status != "succeeded"
                or gate.conclusion not in {"pass", "warn"}
                or gate.snapshot_extraction_task_id != gate.extraction_task_id
                or gate.snapshot_source_jd_version_id != gate.source_jd_version_id
            ):
                raise ExtractionValidationInconsistent()
            if (
                gate.snapshot_data_validation_task_id != gate.task_id
                or gate.snapshot_validation_report_id != gate.report_id
                or gate.snapshot_validation_conclusion != gate.conclusion
                or gate.snapshot_bundle_id != gate.bundle_id
            ):
                raise ExtractionValidationInconsistent("validation_snapshot_inconsistent")
            return
        error_type = _VALIDATION_GATE_ERRORS.get(gate.decision)
        if error_type is not None:
            raise error_type()
        raise ExtractionValidationInconsistent(gate.decision)

    def get_imported_draft(self, task_id: str) -> ExtractionDraftRecord:
        with self._uow_factory() as uow:
            if uow.tasks.get(task_id) is None:
                raise ExtractionTaskNotFound("ExtractionTask not found")
            draft = uow.drafts.get_by_task(task_id)
            if draft is None:
                raise ExtractionTaskNotFound("Imported JD draft not found")
            return draft

    def list_imported_drafts(self, source_jd_version_id: str) -> tuple[ExtractionDraftRecord, ...]:
        with self._uow_factory() as uow:
            if uow.sources.get_version(source_jd_version_id) is None:
                raise ExtractionTaskNotFound("SourceJDVersion not found")
            return uow.drafts.list_by_source_version(source_jd_version_id)

    def run_extraction_task(
        self, task_id: str, claimed_by: str | None = None
    ) -> ExtractionTaskRecord:
        claimed = self._claim(task_id, retry=claimed_by is not None, claimed_by=claimed_by)
        if claimed.status == "succeeded":
            if self._data_validation_mode != "off":
                return self._ensure_current_validation_task(task_id)
            return claimed
        return self._execute_claimed(claimed)

    def retry_extraction_task(self, task_id: str) -> ExtractionTaskRecord:
        claimed = self._claim(task_id, retry=True, claimed_by=None)
        if claimed.status == "succeeded":
            if self._data_validation_mode != "off":
                return self._ensure_current_validation_task(task_id)
            return claimed
        result = self._execute_claimed(claimed)
        return result

    def _execute_claimed(self, claimed: ExtractionTaskRecord) -> ExtractionTaskRecord:
        try:
            envelope = self._load_envelope(claimed.source_jd_version_id)
            bundle = self._provider_for_mode(claimed.extraction_mode).extract(envelope)
            validated = parse_extracted_jd_bundle(bundle.model_dump(mode="json"))
            validate_bundle_matches_envelope(envelope, validated)
        except ExtractionProviderError as exc:
            return self._fail(
                claimed.id,
                exc.code,
                "Extraction provider reported a failure.",
                exc.retryable,
            )
        except (ValidationError, ValueError, TypeError):
            return self._fail(
                claimed.id,
                "extraction_bundle_contract_mismatch",
                "Extraction result failed contract or source identity validation.",
                False,
            )
        except Exception:
            return self._fail(
                claimed.id,
                "extraction_provider_internal",
                "Extraction provider failed unexpectedly.",
                True,
            )
        return self._succeed(claimed.id, validated)

    def claim_next_extraction_task(
        self, worker_id: str, lease_timeout_seconds: float
    ) -> ExtractionTaskRecord | None:
        now = self._clock()
        with self._uow_factory() as uow:
            uow.acquire_task_lock("claim-next-extraction-task")
            task = uow.tasks.claim_next(
                worker_id,
                now,
                now + timedelta(seconds=lease_timeout_seconds),
            )
            if task is not None:
                uow.commit()
            return task

    def heartbeat_extraction_task(
        self, task_id: str, worker_id: str, lease_timeout_seconds: float
    ) -> bool:
        now = self._clock()
        with self._uow_factory() as uow:
            changed = uow.tasks.renew_lease(
                task_id,
                worker_id,
                now,
                now + timedelta(seconds=lease_timeout_seconds),
            )
            if changed:
                uow.commit()
            return changed

    def recover_stale_extraction_tasks(self) -> int:
        with self._uow_factory() as uow:
            uow.acquire_task_lock("recover-stale-extraction-tasks")
            count = uow.tasks.recover_stale(self._clock())
            if count:
                uow.commit()
            return count

    def _create_task_in_uow(
        self, uow, version_id: str, extraction_mode: ExtractionMode
    ) -> tuple[ExtractionTaskRecord, bool]:
        provider = self._provider_for_mode(extraction_mode)
        existing = uow.tasks.get_by_idempotency_key(version_id, provider.request_id)
        if existing is not None:
            return existing, False
        task = uow.tasks.add(
            version_id,
            extraction_mode,
            provider.name,
            provider.request_id,
            self._max_attempts,
        )
        return task, True

    def _provider_for_mode(self, extraction_mode: ExtractionMode) -> JDExtractionProvider:
        if extraction_mode not in {"llm", "rule"}:
            raise ValueError("extraction_mode must be llm or rule")
        provider = self._providers.get(extraction_mode)
        if provider is None:
            raise ExtractionProviderError(
                "extraction_provider_not_configured",
                f"Extraction mode {extraction_mode} is not configured.",
                retryable=False,
            )
        return provider

    def _claim(self, task_id: str, *, retry: bool, claimed_by: str | None) -> ExtractionTaskRecord:
        with self._uow_factory() as uow:
            uow.acquire_task_lock(f"run:{task_id}")
            task = uow.tasks.get(task_id, for_update=True)
            if task is None:
                raise ExtractionTaskNotFound("ExtractionTask not found")
            if task.claimed_by is not None and task.claimed_by != claimed_by:
                raise ExtractionTaskConflict("ExtractionTask is leased by another worker")
            if task.status == "succeeded":
                return task
            if task.status == "running":
                raise ExtractionTaskConflict("ExtractionTask is already running")
            if task.status == "failed":
                if not retry:
                    raise ExtractionTaskRetryRejected("Failed task must use retry")
                if not task.retryable:
                    raise ExtractionTaskRetryRejected("ExtractionTask is not retryable")
            if task.attempt_count >= task.max_attempts:
                raise ExtractionTaskRetryRejected("ExtractionTask exhausted max attempts")
            claimed = uow.tasks.mark_running(task_id, self._clock())
            uow.commit()
            return claimed

    def _load_envelope(self, version_id: str) -> CrawlerJDEnvelopeV1:
        with self._uow_factory() as uow:
            version = uow.sources.get_version(version_id)
            if version is None:
                raise ExtractionTaskNotFound("SourceJDVersion not found")
            source = uow.sources.get_source(version.source_jd_id)
            if source is None:
                raise ExtractionTaskNotFound("SourceJD not found")
        return self._envelope(source, version)

    @staticmethod
    def _envelope(source, version) -> CrawlerJDEnvelopeV1:
        return CrawlerJDEnvelopeV1(
            schema_version=version.schema_version,
            source_record_id=source.source_record_id,
            source_platform=source.source_platform,
            source_url=version.source_url,
            job_title_raw=version.job_title_raw,
            company_name_raw=version.company_name_raw,
            region_raw=version.region_raw,
            publish_time_raw=version.publish_time_raw,
            crawl_time=version.crawl_time,
            raw_text=version.raw_text,
            raw_payload=thaw_json_object(version.raw_payload),
            raw_html=version.raw_html,
            content_hash=version.content_hash,
            text_canonicalization_version=version.text_canonicalization_version,
            source_version=version.source_version,
        )

    def _succeed(self, task_id: str, bundle: ExtractedJDBundleV1) -> ExtractionTaskRecord:
        payload = freeze_json_object(bundle.model_dump(mode="json"), field="bundle")
        with self._uow_factory() as uow:
            uow.acquire_task_lock(f"finish:{task_id}")
            task = uow.tasks.get(task_id, for_update=True)
            if task is None:
                raise ExtractionTaskNotFound("ExtractionTask not found")
            if task.status != "running":
                raise ExtractionTaskConflict("ExtractionTask is no longer running")
            result = uow.tasks.mark_succeeded(task_id, payload, self._clock())
            if self._data_validation_mode != "off":
                uow.validation_tasks.ensure_for_extraction(
                    extraction_task_id=result.id,
                    source_jd_version_id=result.source_jd_version_id,
                    bundle_payload=payload,
                )
            uow.commit()
            return result

    def _fail(self, task_id: str, code: str, message: str, retryable: bool) -> ExtractionTaskRecord:
        with self._uow_factory() as uow:
            uow.acquire_task_lock(f"finish:{task_id}")
            task = uow.tasks.get(task_id, for_update=True)
            if task is None:
                raise ExtractionTaskNotFound("ExtractionTask not found")
            if task.status != "running":
                raise ExtractionTaskConflict("ExtractionTask is no longer running")
            can_retry = retryable and task.attempt_count < task.max_attempts
            result = uow.tasks.mark_failed(task_id, code, message, can_retry, self._clock())
            uow.commit()
            return result
