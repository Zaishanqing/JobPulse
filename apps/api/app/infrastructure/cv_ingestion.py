from __future__ import annotations

import json
import logging
import socket
import ssl
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx
from pydantic import ValidationError
from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.contexts.cv_ingestion import (
    CVExtractionTaskRecord,
    SourceCVImportResult,
    ValidatedCVSnapshotRecord,
)
from app.contexts.cv_ingestion.domain import SourceCVVersionRecord
from app.contexts.talent_acquisition import ManageResumes
from app.domain.accounts import AccountActor
from app.domain.json_types import (
    FrozenJsonObject,
    freeze_json_object,
    thaw_json_object,
)
from app.models.source_cv import (
    CVExtractionTask,
    SourceCV,
    SourceCVVersion,
    ValidatedCVSnapshot,
)
from app.models.data_validation import CVDataValidationTask, CVValidationReport
from app.models.resume import Resume
from jobgraph_contracts.cv_extraction_http import (
    CVExtractionResponseV3,
    parse_cv_extraction_response,
)


LOGGER = logging.getLogger(__name__)


class CVExtractionProviderError(RuntimeError):
    code = "CV_EXTRACTION_PROVIDER_UNAVAILABLE"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        model: str | None = None,
    ):
        super().__init__(message)
        if code is not None:
            self.code = code
        self.model = model


def _provider_transport_reason(exc: BaseException) -> str:
    cause = exc.__cause__
    while cause is not None:
        if isinstance(cause, ssl.SSLError):
            return "tls"
        if isinstance(cause, socket.gaierror):
            return "dns"
        cause = cause.__cause__
    return "connect"


def _provider_status_code(status_code: int) -> str:
    if status_code in (401, 403):
        return "CV_EXTRACTION_AUTH_FAILED"
    if status_code == 404:
        return "CV_EXTRACTION_MODEL_NOT_AVAILABLE"
    if status_code == 429:
        return "CV_EXTRACTION_RATE_LIMITED"
    return "CV_EXTRACTION_PROVIDER_UNAVAILABLE"


def default_cv_taxonomy_version() -> str:
    catalog_path = (
        Path(__file__).resolve().parents[2]
        / "config"
        / "skill_taxonomy_catalog.v1.json"
    )
    snapshot = json.loads(catalog_path.read_text(encoding="utf-8"))
    del snapshot
    return "skill-taxonomy-snapshot.v1"


class HttpCVExtractionProvider:
    def __init__(
        self,
        base_url: str | None,
        internal_token: str | None,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        *,
        provider_name: str,
        model_name: str,
        prompt_version: str,
        schema_version: str,
        normalization_version: str,
        validation_policy_version: str,
        taxonomy_version: str | None = None,
    ) -> None:
        self._base_url = (base_url or "").rstrip("/")
        self._token = internal_token or ""
        self._read_timeout_seconds = read_timeout_seconds
        self._timeout = httpx.Timeout(
            connect=connect_timeout_seconds,
            read=read_timeout_seconds,
            write=read_timeout_seconds,
            pool=connect_timeout_seconds,
        )
        self._request_material = {
            "contract_version": "cv-extraction-http.v3",
            "provider": provider_name,
            "model": model_name,
            "prompt_version": prompt_version,
            "schema_version": schema_version,
            "normalization_version": normalization_version,
            "taxonomy_version": taxonomy_version or default_cv_taxonomy_version(),
            "validation_policy_version": validation_policy_version,
        }

    @property
    def request_id(self) -> str:
        return ":".join(
            str(self._request_material[key])
            for key in ("provider", "model", "prompt_version", "schema_version", "normalization_version", "taxonomy_version")
        )

    def extract(
        self,
        *,
        document_id: str,
        raw_text: str,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> FrozenJsonObject:
        if not self._base_url or not self._token:
            raise CVExtractionProviderError("CV extraction provider is not configured")
        started = perf_counter()
        LOGGER.info(
            "cv_extraction_http_started document_id=%s read_timeout_seconds=%s",
            document_id,
            self._read_timeout_seconds,
        )
        try:
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="cv-http") as executor:
                future = executor.submit(
                    httpx.post,
                    f"{self._base_url}/api/v3/cv-extractions",
                    json={"document_id": document_id, "raw_text": raw_text},
                    headers={"X-Internal-Token": self._token},
                    timeout=self._timeout,
                )
                while True:
                    try:
                        response = future.result(timeout=0.75)
                        break
                    except FutureTimeoutError:
                        if progress_callback is None:
                            continue
                        try:
                            progress_response = httpx.get(
                                f"{self._base_url}/api/v3/cv-extractions/{document_id}/progress",
                                headers={"X-Internal-Token": self._token},
                                timeout=httpx.Timeout(connect=3, read=3, write=3, pool=3),
                            )
                        except httpx.TransportError:
                            LOGGER.warning(
                                "cv_extraction_progress_poll_failed document_id=%s",
                                document_id,
                            )
                            continue
                        if progress_response.status_code == 200:
                            progress_body = progress_response.json()
                            progress = progress_body.get("data")
                            if isinstance(progress, dict):
                                progress_callback(progress)
        except httpx.TimeoutException as exc:
            LOGGER.error(
                "cv_extraction_http_timed_out document_id=%s duration_ms=%s read_timeout_seconds=%s",
                document_id,
                int(round((perf_counter() - started) * 1000)),
                self._read_timeout_seconds,
            )
            raise CVExtractionProviderError(
                "CV extraction provider timed out",
                code="CV_EXTRACTION_PROVIDER_TIMEOUT",
            ) from exc
        except httpx.TransportError as exc:
            reason = _provider_transport_reason(exc)
            LOGGER.error(
                "cv_extraction_http_transport_failed document_id=%s duration_ms=%s reason=%s",
                document_id,
                int(round((perf_counter() - started) * 1000)),
                reason,
            )
            raise CVExtractionProviderError(
                f"CV extraction provider connection failed ({reason})",
                code="CV_EXTRACTION_PROVIDER_CONNECTION_FAILED",
            ) from exc
        if response.status_code != 200:
            detail = self._non_ok_detail(response)
            if detail is not None:
                code, message = detail
            else:
                code = _provider_status_code(response.status_code)
                if code == "CV_EXTRACTION_MODEL_NOT_AVAILABLE":
                    message = (
                        "CV extraction model "
                        f"{self._request_material['model']} is not available"
                    )
                elif code == "CV_EXTRACTION_AUTH_FAILED":
                    message = "CV extraction provider authentication failed"
                elif code == "CV_EXTRACTION_RATE_LIMITED":
                    message = "CV extraction provider rate limit reached"
                else:
                    message = (
                        f"CV extraction provider returned HTTP {response.status_code}"
                    )
            raise CVExtractionProviderError(
                message,
                code=code,
                model=self._request_material["model"],
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise CVExtractionProviderError(
                "CV extraction provider returned invalid JSON",
                code="CV_EXTRACTION_PROVIDER_INVALID_RESPONSE",
            ) from exc
        data = body.get("data") if isinstance(body, dict) else None
        if (
            not isinstance(body, dict)
            or body.get("code") != 0
            or not isinstance(data, dict)
        ):
            raise CVExtractionProviderError(
                "CV extraction provider contract is invalid",
                code="CV_EXTRACTION_CONTRACT_INVALID",
            )
        try:
            parsed = CVExtractionResponseV3.model_validate(data)
        except ValidationError as exc:
            raise CVExtractionProviderError(
                "CV extraction provider response failed strict contract validation",
                code="CV_EXTRACTION_CONTRACT_INVALID",
            ) from exc
        if parsed.document_id != document_id:
            raise CVExtractionProviderError(
                "CV extraction provider returned a different document_id",
                code="CV_EXTRACTION_CONTRACT_INVALID",
            )
        actual = parsed.execution.model_dump(mode="json")
        expected = {
            key: value
            for key, value in self._request_material.items()
            if key not in {"validation_policy_version", "contract_version"}
        }
        mismatches = {
            key: {"expected": expected[key], "actual": actual.get(key)}
            for key in expected
            if actual.get(key) != expected[key]
        }
        if mismatches:
            raise CVExtractionProviderError(
                "CV extraction execution metadata does not match configured expectations: "
                + json.dumps(mismatches, ensure_ascii=False, sort_keys=True),
                code="CV_EXTRACTION_CONTRACT_INVALID",
            )
        LOGGER.info(
            "cv_extraction_http_completed document_id=%s duration_ms=%s",
            document_id,
            int(round((perf_counter() - started) * 1000)),
        )
        return freeze_json_object(
            parsed.model_dump(mode="json"), field="cv_extraction_response"
        )

    def load_demo_snapshot(
        self, *, dataset_version: str, document_id: str
    ) -> tuple[str, FrozenJsonObject]:
        if not self._base_url or not self._token:
            raise CVExtractionProviderError("CV demo snapshot service is not configured")
        try:
            response = httpx.get(
                f"{self._base_url}/api/v1/demo-cv-snapshots/{dataset_version}",
                headers={"X-Internal-Token": self._token},
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise CVExtractionProviderError(
                "CV demo snapshot service timed out",
                code="CV_DEMO_SNAPSHOT_TIMEOUT",
            ) from exc
        except httpx.TransportError as exc:
            raise CVExtractionProviderError(
                "CV demo snapshot service connection failed",
                code="CV_DEMO_SNAPSHOT_UNAVAILABLE",
            ) from exc
        if response.status_code != 200:
            raise CVExtractionProviderError(
                f"CV demo snapshot service returned HTTP {response.status_code}",
                code=(
                    "CV_DEMO_SNAPSHOT_NOT_FOUND"
                    if response.status_code == 404
                    else "CV_DEMO_SNAPSHOT_UNAVAILABLE"
                ),
            )
        body = response.json()
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(body, dict) or body.get("code") != 0 or not isinstance(data, dict):
            raise CVExtractionProviderError(
                "CV demo snapshot response is invalid",
                code="CV_DEMO_SNAPSHOT_INVALID",
            )
        source_text = str(data.pop("source_text", "")).strip()
        if not source_text:
            raise CVExtractionProviderError(
                "CV demo snapshot source text is missing",
                code="CV_DEMO_SNAPSHOT_INVALID",
            )
        rebound = self._rebind_document_id(data, document_id)
        try:
            parsed = parse_cv_extraction_response(rebound)
        except (ValidationError, TypeError, ValueError) as exc:
            raise CVExtractionProviderError(
                "CV demo snapshot failed strict contract validation",
                code="CV_DEMO_SNAPSHOT_INVALID",
            ) from exc
        execution = parsed.execution
        if not (
            execution.mode == "demo_snapshot"
            and execution.provider == "jobgraph_demo_data"
            and execution.is_demo is True
            and execution.dataset_version == dataset_version
        ):
            raise CVExtractionProviderError(
                "CV demo snapshot lineage is invalid",
                code="CV_DEMO_SNAPSHOT_INVALID",
            )
        return source_text, freeze_json_object(
            parsed.model_dump(mode="json"), field="cv_demo_snapshot_response"
        )

    @classmethod
    def _rebind_document_id(cls, value, document_id: str):
        if isinstance(value, dict):
            return {
                key: document_id
                if key in {"document_id", "source_document_id"}
                else cls._rebind_document_id(item, document_id)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._rebind_document_id(item, document_id) for item in value]
        return value

    @staticmethod
    def _non_ok_detail(
        response: httpx.Response,
    ) -> tuple[str, str] | None:
        try:
            body = response.json()
        except ValueError:
            return None
        if not isinstance(body, dict):
            return None
        detail = body.get("detail")
        if not isinstance(detail, dict):
            return None
        code = detail.get("code")
        message = detail.get("message")
        if not isinstance(code, str) or not code.startswith(
            ("CV_EXTRACTION_", "CV_EVIDENCE_")
        ):
            return None
        return code, str(message) if message is not None else "CV extraction provider failed"


class ApplicationResumeImporter:
    def __init__(self, resumes: ManageResumes) -> None:
        self._resumes = resumes

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
        resume_id: str | None = None,
    ) -> str:
        record = self._resumes.import_validated_cv(
            actor,
            validated_cv_snapshot_id=validated_cv_snapshot_id,
            source_cv_version_id=source_cv_version_id,
            raw_text=raw_text,
            extraction_payload=extraction_payload,
            normalized_payload=normalized_payload,
            review_flags=review_flags,
            resume_id=resume_id,
        )
        return record.resume_id

    def publish_profile_refresh(
        self,
        resume_id: str,
        event_type: str,
        *,
        snapshot_id: str | None = None,
        snapshot_revision: int | None = None,
        source_version: str | None = None,
    ) -> None:
        self._resumes.publish_profile_refresh(
            resume_id,
            event_type,
            snapshot_id=snapshot_id,
            snapshot_revision=snapshot_revision,
            source_version=source_version,
        )


class SqlAlchemyCVIngestionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

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
    ) -> SourceCVImportResult:
        source = (
            self._session.query(SourceCV)
            .filter(
                SourceCV.owner_id == owner_id,
                SourceCV.source_platform == source_platform,
                SourceCV.source_record_id == source_record_id,
            )
            .one_or_none()
        )
        created_source = source is None
        if source is None:
            source = SourceCV(
                owner_id=owner_id,
                source_platform=source_platform,
                source_record_id=source_record_id,
            )
            self._session.add(source)
            self._session.flush()
        version = (
            self._session.query(SourceCVVersion)
            .filter(
                SourceCVVersion.source_cv_id == source.id,
                SourceCVVersion.source_version == source_version,
            )
            .one_or_none()
        )
        created_version = version is None
        if version is None:
            version = SourceCVVersion(
                source_cv_id=source.id,
                raw_text=raw_text,
                source_version=source_version,
            )
            self._session.add(version)
            self._session.flush()
        task = (
            self._session.query(CVExtractionTask)
            .filter(
                CVExtractionTask.source_cv_version_id == version.id,
                CVExtractionTask.request_id == request_id,
            )
            .one_or_none()
        )
        created_task = task is None
        if task is None:
            task = CVExtractionTask(
                source_cv_version_id=version.id,
                request_id=request_id,
                max_attempts=max_attempts,
            )
            self._session.add(task)
            self._session.flush()
        return SourceCVImportResult(
            source.id,
            version.id,
            task.id,
            created_source,
            created_version,
            created_task,
            task.status,
        )

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
    ) -> SourceCVImportResult:
        source = (
            self._session.query(SourceCV)
            .filter(
                SourceCV.owner_id == owner_id,
                SourceCV.source_platform == source_platform,
                SourceCV.source_record_id == source_record_id,
            )
            .one_or_none()
        )
        created_source = source is None
        if source is None:
            source = SourceCV(
                owner_id=owner_id,
                source_platform=source_platform,
                source_record_id=source_record_id,
            )
            self._session.add(source)
            self._session.flush()
        version = (
            self._session.query(SourceCVVersion)
            .filter(
                SourceCVVersion.source_cv_id == source.id,
                SourceCVVersion.source_version == source_version,
            )
            .one_or_none()
        )
        created_version = version is None
        if version is None:
            version = SourceCVVersion(
                source_cv_id=source.id,
                source_file_id=source_file_id,
                original_filename=original_filename,
                content_type=content_type,
                extraction_method=extraction_method,
                extraction_provider=extraction_provider,
                extraction_provider_version=extraction_provider_version,
                text_extraction_status=text_extraction_status,
                page_count=page_count,
                quality_flags=list(quality_flags),
                ocr_layout=(
                    [thaw_json_object(item) for item in ocr_layout]
                    if ocr_layout is not None
                    else None
                ),
                raw_text=raw_text,
                source_version=source_version,
            )
            self._session.add(version)
            self._session.flush()
        task = (
            self._session.query(CVExtractionTask)
            .filter(
                CVExtractionTask.source_cv_version_id == version.id,
                CVExtractionTask.request_id == request_id,
            )
            .one_or_none()
        )
        created_task = task is None
        if task is None:
            task = CVExtractionTask(
                source_cv_version_id=version.id,
                request_id=request_id,
                max_attempts=max_attempts,
            )
            self._session.add(task)
            self._session.flush()
        return SourceCVImportResult(
            source.id,
            version.id,
            task.id,
            created_source,
            created_version,
            created_task,
            task.status,
            text_extraction_status=text_extraction_status,
            extraction_method=extraction_method,
            extraction_provider=extraction_provider,
            source_file_id=source_file_id,
        )

    def get_task(self, task_id: str) -> CVExtractionTaskRecord | None:
        row = self._session.get(CVExtractionTask, task_id)
        return self._task(row) if row is not None else None

    def schedule_reextraction(
        self,
        source_cv_version_id: str,
        *,
        request_id: str,
        max_attempts: int,
    ) -> CVExtractionTaskRecord:
        """Create a new extraction attempt while preserving source/version lineage."""
        if self._session.get(SourceCVVersion, source_cv_version_id) is None:
            raise ValueError("SourceCVVersion not found")
        task = CVExtractionTask(
            source_cv_version_id=source_cv_version_id,
            request_id=request_id,
            max_attempts=max_attempts,
        )
        self._session.add(task)
        self._session.flush()
        return self._task(task)

    def get_version(self, version_id: str) -> SourceCVVersionRecord | None:
        row = self._session.get(SourceCVVersion, version_id)
        if row is None:
            return None
        source = self._session.get(SourceCV, row.source_cv_id)
        if source is None:
            raise RuntimeError("SourceCVVersion has no source")
        return SourceCVVersionRecord(
            row.id,
            row.source_cv_id,
            source.owner_id,
            row.raw_text,
            row.source_version,
            row.created_at,
            source_file_id=row.source_file_id,
            original_filename=row.original_filename,
            content_type=row.content_type,
            extraction_method=row.extraction_method,
            extraction_provider=row.extraction_provider,
            extraction_provider_version=row.extraction_provider_version,
            text_extraction_status=row.text_extraction_status,
            page_count=row.page_count,
            quality_flags=tuple(row.quality_flags or ()),
            ocr_layout=(
                tuple(
                    freeze_json_object(item, field="cv_ocr_layout")
                    for item in row.ocr_layout
                )
                if isinstance(row.ocr_layout, list)
                else None
            ),
        )

    def mark_failed(
        self, task_id: str, *, worker_id: str, code: str, message: str
    ) -> CVExtractionTaskRecord:
        row = self._required_task(task_id)
        self._require_lease(row, worker_id)
        self._transition_stage(row, "failed", datetime.now(timezone.utc))
        row.status = "failed"
        row.last_error_code = code
        row.last_error_message = message
        row.retryable = row.attempt_count < row.max_attempts
        row.next_attempt_at = (
            datetime.now(timezone.utc) + timedelta(seconds=min(300, 2**row.attempt_count))
            if row.retryable
            else None
        )
        row.claimed_by = None
        row.lease_expires_at = None
        row.heartbeat_at = None
        if not row.retryable:
            row.finished_at = datetime.now(timezone.utc)
        self._session.flush()
        return self._task(row)

    def complete_without_snapshot(
        self, task_id: str, *, worker_id: str, conclusion: str, report: FrozenJsonObject
    ) -> CVExtractionTaskRecord:
        if conclusion not in {"warn", "block"}:
            raise ValueError("Non-allow CV validation requires warn or block")
        row = self._required_task(task_id)
        self._require_lease(row, worker_id)
        row.status = "succeeded"
        row.validation_conclusion = conclusion
        row.validation_report_payload = thaw_json_object(report)
        row.claimed_by = None
        row.lease_expires_at = None
        row.heartbeat_at = None
        row.next_attempt_at = None
        row.finished_at = datetime.now(timezone.utc)
        self._session.flush()
        return self._task(row)

    def complete_with_review_pending(
        self,
        task_id: str,
        *,
        worker_id: str,
        conclusion: str,
        report: FrozenJsonObject,
        review_payload: FrozenJsonObject,
        review_id: str,
    ) -> CVExtractionTaskRecord:
        if conclusion not in {"pass", "warn", "block"}:
            raise ValueError("Unsupported CV validation conclusion")
        row = self._required_task(task_id)
        self._require_lease(row, worker_id)
        self._transition_stage(row, "review_pending", datetime.now(timezone.utc))
        row.status = "succeeded"
        row.validation_conclusion = conclusion
        row.validation_report_payload = thaw_json_object(report)
        row.review_payload = thaw_json_object(review_payload)
        row.review_id = review_id
        row.confirmation_status = "pending"
        row.claimed_by = None
        row.lease_expires_at = None
        row.heartbeat_at = None
        row.next_attempt_at = None
        row.finished_at = datetime.now(timezone.utc)
        self._session.flush()
        return self._task(row)

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
    ) -> ValidatedCVSnapshotRecord:
        if conclusion not in {"pass", "warn"}:
            raise ValueError("Validated CV snapshot requires an allow or review decision")
        row = self._required_task(task_id)
        if row.status != "succeeded":
            raise ValueError("CV extraction task must be succeeded before confirmation")
        snapshot_id = str(uuid4())
        existing = self._session.get(ValidatedCVSnapshot, snapshot_id)
        if existing is not None:
            return self._snapshot(existing)
        snapshot = ValidatedCVSnapshot(
            id=snapshot_id,
            cv_extraction_task_id=task_id,
            source_cv_version_id=row.source_cv_version_id,
            source_file_id=source_file_id,
            snapshot_revision=snapshot_revision,
            supersedes_snapshot_id=supersedes_snapshot_id,
            confirmed_by=actor_id,
            confirmed_at=confirmed_at,
            extraction_provider=extraction_provider,
            model=model,
            prompt_version=prompt_version,
            extraction_schema_version=extraction_schema_version,
            normalization_version=normalization_version,
            taxonomy_version=taxonomy_version,
            field_decisions=[thaw_json_object(item) for item in field_decisions],
            evidence_payload=thaw_json_object(evidence_payload),
            validation_report_id=self._required_validation_report_id(task_id),
            policy_version=policy_version,
            conclusion=conclusion,
            extraction_payload=thaw_json_object(extraction_payload),
            normalized_payload=thaw_json_object(normalized_payload),
            findings_payload=thaw_json_object(findings_payload),
            execution_metadata=thaw_json_object(execution_metadata),
        )
        self._session.add(snapshot)
        row.validation_conclusion = conclusion
        row.validation_report_payload = thaw_json_object(report)
        self._session.flush()
        return self._snapshot(snapshot)

    def complete_confirmation(
        self,
        task_id: str,
        *,
        actor_id: str,
        snapshot_id: str,
        idempotency_key: str,
        idempotency_id: str,
        confirmed_at,
    ) -> CVExtractionTaskRecord:
        row = self._required_task(task_id)
        self._transition_stage(row, "succeeded", confirmed_at)
        row.confirmation_status = "confirmed"
        row.latest_validated_cv_snapshot_id = snapshot_id
        row.confirmed_at = confirmed_at
        row.confirmed_by = actor_id
        row.review_revision += 1
        row.confirmation_idempotency_key = idempotency_key
        row.confirmation_idempotency_id = idempotency_id
        self._session.flush()
        return self._task(row)

    def get_snapshot(self, snapshot_id: str) -> ValidatedCVSnapshotRecord | None:
        row = self._session.get(ValidatedCVSnapshot, snapshot_id)
        if row is None:
            return None
        resume_row = (
            self._session.query(Resume.id)
            .filter(Resume.validated_cv_snapshot_id == row.id)
            .one_or_none()
        )
        resume_id = resume_row[0] if resume_row is not None else None
        return self._snapshot(row, resume_id=resume_id)

    def next_snapshot_revision(self, task_id: str) -> int:
        count = (
            self._session.query(ValidatedCVSnapshot.id)
            .filter(ValidatedCVSnapshot.cv_extraction_task_id == task_id)
            .count()
        )
        return count + 1

    def complete_with_resume(
        self, task_id: str, *, worker_id: str, resume_id: str
    ) -> CVExtractionTaskRecord:
        row = self._required_task(task_id)
        snapshot = self.get_snapshot_by_task(task_id)
        if snapshot is None:
            raise ValueError("Resume draft requires an authoritative CV snapshot")
        if row.status == "succeeded":
            if row.resume_id != resume_id:
                raise ValueError("Validated CV completion identity mismatch")
            return self._task(row)
        self._require_lease(row, worker_id)
        row.status = "succeeded"
        row.resume_id = resume_id
        row.retryable = False
        row.claimed_by = None
        row.lease_expires_at = None
        row.heartbeat_at = None
        row.next_attempt_at = None
        row.finished_at = datetime.now(timezone.utc)
        self._session.flush()
        return self._task(row)

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
    ) -> tuple[str, str]:
        if conclusion not in {"pass", "warn", "block"}:
            raise ValueError("Unsupported CV validation conclusion")
        row = self._required_task(task_id)
        self._require_lease(row, worker_id)
        validation_task_id = str(uuid4())
        validation_report_id = str(uuid4())
        validation_task = self._session.get(CVDataValidationTask, validation_task_id)
        if validation_task is None:
            self._session.add(
                CVDataValidationTask(
                    id=validation_task_id,
                    cv_extraction_task_id=task_id,
                    source_cv_version_id=row.source_cv_version_id,
                    policy_version=policy_version,
                    status="succeeded",
                )
            )
            self._session.flush()
        report_row = self._session.get(CVValidationReport, validation_report_id)
        payload = thaw_json_object(report)
        if report_row is None:
            self._session.add(
                CVValidationReport(
                    id=validation_report_id,
                    cv_data_validation_task_id=validation_task_id,
                    conclusion=conclusion,
                    policy_version=policy_version,
                    report_payload=payload,
                )
            )
            self._session.flush()
        elif report_row.conclusion != conclusion or report_row.report_payload != payload:
            raise ValueError("CV validation report identity mismatch")
        row.validation_conclusion = conclusion
        row.validation_report_payload = payload
        provider_execution = thaw_json_object(execution_metadata)
        task_execution = dict(row.execution_metadata or {})
        provider_execution["task_stages"] = list(
            task_execution.get("task_stages") or []
        )
        provider_execution["current_stage"] = task_execution.get(
            "current_stage", "contract_validating"
        )
        row.execution_metadata = provider_execution
        row.execution_id = execution_id
        self._session.flush()
        return validation_task_id, validation_report_id

    def claim(
        self,
        task_id: str,
        *,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> CVExtractionTaskRecord | None:
        claimed_id = self._session.execute(
            update(CVExtractionTask)
            .where(
                CVExtractionTask.id == task_id,
                self._claimable(now),
                CVExtractionTask.attempt_count < CVExtractionTask.max_attempts,
            )
            .values(
                status="running",
                attempt_count=CVExtractionTask.attempt_count + 1,
                claimed_by=worker_id,
                lease_expires_at=lease_expires_at,
                heartbeat_at=now,
                retryable=False,
                last_error_code=None,
                last_error_message=None,
            )
            .returning(CVExtractionTask.id)
        ).scalar_one_or_none()
        if claimed_id is None:
            return None
        row = self._required_task(claimed_id)
        self._transition_stage(row, "extracting", now)
        self._session.flush()
        return self._task(row)

    def record_processing_stage(
        self,
        task_id: str,
        *,
        worker_id: str,
        stage: str,
        now: datetime,
    ) -> CVExtractionTaskRecord:
        if stage not in {
            "extracting",
            "contract_validating",
            "semantic_repairing",
            "review_pending",
        }:
            raise ValueError("Unsupported CV processing stage")
        row = self._required_task(task_id)
        self._require_lease(row, worker_id)
        self._transition_stage(row, stage, now)
        self._session.flush()
        return self._task(row)

    def record_extraction_progress(
        self,
        task_id: str,
        *,
        worker_id: str,
        progress: FrozenJsonObject,
    ) -> CVExtractionTaskRecord:
        row = self._required_task(task_id)
        self._require_lease(row, worker_id)
        metadata = dict(row.execution_metadata or {})
        metadata["progress_detail"] = thaw_json_object(progress)
        stage = metadata["progress_detail"].get("stage")
        if stage in {"semantic_validating", "contract_validating"}:
            metadata["current_stage"] = "semantic_repairing"
        else:
            metadata["current_stage"] = "extracting"
        row.execution_metadata = metadata
        self._session.flush()
        return self._task(row)

    @staticmethod
    def _transition_stage(row: CVExtractionTask, stage: str, now: datetime) -> None:
        metadata = dict(row.execution_metadata or {})
        stages = [
            dict(item)
            for item in metadata.get("task_stages", [])
            if isinstance(item, dict)
        ]
        current = metadata.get("current_stage")
        if current == stage:
            return
        if stages and stages[-1].get("finished_at") is None:
            stages[-1]["finished_at"] = now.isoformat()
            started_at = stages[-1].get("started_at")
            if isinstance(started_at, str):
                try:
                    started = datetime.fromisoformat(started_at)
                except ValueError:
                    started = None
                if started is not None:
                    stages[-1]["duration_ms"] = max(
                        0, int((now - started).total_seconds() * 1000)
                    )
        stages.append(
            {
                "stage": stage,
                "started_at": now.isoformat(),
                "attempt": int(row.attempt_count or 0),
            }
        )
        metadata["current_stage"] = stage
        metadata["task_stages"] = stages
        row.execution_metadata = metadata

    def claim_next(
        self, *, worker_id: str, now: datetime, lease_expires_at: datetime
    ) -> CVExtractionTaskRecord | None:
        candidate = self._session.execute(
            select(CVExtractionTask.id)
            .where(
                self._claimable(now),
                CVExtractionTask.attempt_count < CVExtractionTask.max_attempts,
            )
            .order_by(CVExtractionTask.created_at.asc(), CVExtractionTask.id.asc())
            .limit(1)
        ).scalar_one_or_none()
        if candidate is None:
            return None
        return self.claim(
            candidate,
            worker_id=worker_id,
            now=now,
            lease_expires_at=lease_expires_at,
        )

    def heartbeat(
        self,
        task_id: str,
        *,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        changed = self._session.execute(
            update(CVExtractionTask)
            .where(
                CVExtractionTask.id == task_id,
                CVExtractionTask.status == "running",
                CVExtractionTask.claimed_by == worker_id,
                CVExtractionTask.lease_expires_at > now,
            )
            .values(heartbeat_at=now, lease_expires_at=lease_expires_at)
        ).rowcount
        return changed == 1

    def recover_stale(self, *, now: datetime) -> int:
        rows = (
            self._session.execute(
                select(CVExtractionTask).where(
                    CVExtractionTask.status == "running",
                    CVExtractionTask.lease_expires_at <= now,
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.status = "failed"
            row.retryable = row.attempt_count < row.max_attempts
            row.next_attempt_at = now if row.retryable else None
            row.last_error_code = "lease_expired"
            row.last_error_message = "CV extraction worker lease expired"
            row.claimed_by = None
            row.lease_expires_at = None
            row.heartbeat_at = None
            if not row.retryable:
                row.finished_at = now
        self._session.flush()
        return len(rows)

    def retry(self, task_id: str, *, now: datetime) -> CVExtractionTaskRecord:
        row = self._required_task(task_id)
        if row.status == "succeeded":
            return self._task(row)
        if row.status == "pending":
            return self._task(row)
        if row.status == "running":
            raise ValueError("CV extraction task is currently leased")
        if row.attempt_count >= row.max_attempts:
            raise ValueError("CV extraction task exhausted max attempts")
        # Moving back to pending makes the retry observable and prevents the
        # Portal from offering the same action repeatedly before a worker claim.
        row.status = "pending"
        row.retryable = False
        row.next_attempt_at = None
        row.finished_at = None
        self._session.flush()
        return self._task(row)

    def cancel(self, task_id: str, *, now: datetime) -> CVExtractionTaskRecord:
        row = self._required_task(task_id)
        if row.status == "cancelled":
            return self._task(row)
        if row.status == "succeeded":
            raise ValueError("Confirmed or review-ready CV extraction cannot be cancelled")
        row.status = "cancelled"
        row.retryable = False
        row.next_attempt_at = None
        row.claimed_by = None
        row.lease_expires_at = None
        row.heartbeat_at = None
        row.finished_at = now
        self._session.flush()
        return self._task(row)

    @staticmethod
    def _claimable(now: datetime):
        return or_(
            CVExtractionTask.status == "pending",
            and_(
                CVExtractionTask.status == "failed",
                CVExtractionTask.retryable.is_(True),
                or_(
                    CVExtractionTask.next_attempt_at.is_(None),
                    CVExtractionTask.next_attempt_at <= now,
                ),
            ),
        )

    def get_snapshot_by_task(self, task_id: str) -> ValidatedCVSnapshotRecord | None:
        row = (
            self._session.query(ValidatedCVSnapshot)
            .filter(ValidatedCVSnapshot.cv_extraction_task_id == task_id)
            .order_by(ValidatedCVSnapshot.snapshot_revision.desc())
            .limit(1)
            .one_or_none()
        )
        if row is None:
            return None
        resume_row = (
            self._session.query(Resume.id)
            .filter(Resume.validated_cv_snapshot_id == row.id)
            .one_or_none()
        )
        resume_id = resume_row[0] if resume_row is not None else None
        return self._snapshot(row, resume_id=resume_id)

    def find_pending_review_task_for_resume(
        self, resume_id: str, owner_id: str
    ) -> CVExtractionTaskRecord | None:
        """Locate the latest unconfirmed review for the resume's source CV."""
        resume = self._session.get(Resume, resume_id)
        if (
            resume is None
            or resume.user_id != owner_id
            or not resume.validated_cv_snapshot_id
        ):
            return None
        snapshot = self._session.get(
            ValidatedCVSnapshot, resume.validated_cv_snapshot_id
        )
        if snapshot is None:
            return None
        version = self._session.get(SourceCVVersion, snapshot.source_cv_version_id)
        if version is None:
            return None
        row = (
            self._session.query(CVExtractionTask)
            .join(
                SourceCVVersion,
                CVExtractionTask.source_cv_version_id == SourceCVVersion.id,
            )
            .filter(
                SourceCVVersion.source_cv_id == version.source_cv_id,
                CVExtractionTask.status == "succeeded",
                CVExtractionTask.confirmation_status == "pending",
            )
            .order_by(CVExtractionTask.created_at.desc())
            .limit(1)
            .one_or_none()
        )
        if row is None:
            return None
        return self._task(row)

    def _required_task(self, task_id: str) -> CVExtractionTask:
        row = self._session.get(CVExtractionTask, task_id)
        if row is None:
            raise LookupError(task_id)
        return row

    def _required_validation_report_id(self, task_id: str) -> str:
        validation_task = (
            self._session.query(CVDataValidationTask)
            .filter(CVDataValidationTask.cv_extraction_task_id == task_id)
            .one_or_none()
        )
        if validation_task is None:
            raise ValueError("CV validation task is required before snapshot")
        report = (
            self._session.query(CVValidationReport)
            .filter(CVValidationReport.cv_data_validation_task_id == validation_task.id)
            .one_or_none()
        )
        if report is None:
            raise ValueError("CV validation report is required before snapshot")
        return report.id

    @staticmethod
    def _require_lease(row: CVExtractionTask, worker_id: str) -> None:
        if row.status != "running" or row.claimed_by != worker_id:
            raise ValueError("CV extraction task lease is not owned")

    def _task(self, row: CVExtractionTask) -> CVExtractionTaskRecord:
        version = self._session.get(SourceCVVersion, row.source_cv_version_id)
        source = self._session.get(SourceCV, version.source_cv_id) if version else None
        if source is None:
            raise RuntimeError("CV extraction task lineage is incomplete")
        report = (
            freeze_json_object(row.validation_report_payload, field="cv_validation_report")
            if row.validation_report_payload is not None
            else None
        )
        validation_task = (
            self._session.query(CVDataValidationTask)
            .filter(CVDataValidationTask.cv_extraction_task_id == row.id)
            .one_or_none()
        )
        validation_report_id = (
            self._required_validation_report_id(row.id) if validation_task is not None else None
        )
        return CVExtractionTaskRecord(
            row.id,
            row.source_cv_version_id,
            source.owner_id,
            row.request_id,
            row.execution_id,
            (
                freeze_json_object(
                    row.execution_metadata, field="cv_execution_metadata"
                )
                if row.execution_metadata is not None
                else None
            ),
            row.status,
            row.attempt_count,
            row.max_attempts,
            row.last_error_code,
            row.last_error_message,
            row.retryable,
            row.claimed_by,
            row.lease_expires_at,
            row.heartbeat_at,
            row.next_attempt_at,
            row.finished_at,
            row.validation_conclusion,
            report,
            validation_task.id if validation_task else None,
            validation_report_id,
            row.resume_id,
            row.created_at,
            row.updated_at,
            review_payload=(
                freeze_json_object(row.review_payload, field="cv_review_payload")
                if row.review_payload is not None
                else None
            ),
            review_id=row.review_id,
            confirmation_status=row.confirmation_status,
            latest_validated_cv_snapshot_id=row.latest_validated_cv_snapshot_id,
            confirmed_at=row.confirmed_at,
            confirmed_by=row.confirmed_by,
            review_revision=row.review_revision,
            confirmation_idempotency_key=row.confirmation_idempotency_key,
            confirmation_idempotency_id=row.confirmation_idempotency_id,
        )

    @staticmethod
    def _snapshot(
        row: ValidatedCVSnapshot, *, resume_id: str | None = None
    ) -> ValidatedCVSnapshotRecord:
        return ValidatedCVSnapshotRecord(
            row.id,
            row.cv_extraction_task_id,
            row.source_cv_version_id,
            row.validation_report_id,
            row.policy_version,
            row.conclusion,
            freeze_json_object(row.extraction_payload, field="cv_extraction_payload"),
            freeze_json_object(row.normalized_payload, field="cv_normalized_payload"),
            freeze_json_object(row.findings_payload, field="cv_findings"),
            freeze_json_object(
                row.execution_metadata, field="cv_execution_metadata"
            ),
            row.created_at,
            source_file_id=row.source_file_id,
            snapshot_revision=row.snapshot_revision,
            supersedes_snapshot_id=row.supersedes_snapshot_id,
            confirmed_by=row.confirmed_by,
            confirmed_at=row.confirmed_at,
            extraction_provider=row.extraction_provider,
            model=row.model,
            prompt_version=row.prompt_version,
            extraction_schema_version=row.extraction_schema_version,
            normalization_version=row.normalization_version,
            taxonomy_version=row.taxonomy_version,
            field_decisions=(
                tuple(
                    freeze_json_object(item, field="cv_field_decision")
                    for item in row.field_decisions
                )
                if row.field_decisions is not None
                else None
            ),
            evidence_payload=(
                freeze_json_object(row.evidence_payload, field="cv_evidence_payload")
                if row.evidence_payload is not None
                else None
            ),
            resume_id=resume_id,
        )


class SqlAlchemyCVIngestionUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    def __enter__(self) -> "SqlAlchemyCVIngestionUnitOfWork":
        self._session = self._session_factory()
        self.repository = SqlAlchemyCVIngestionRepository(self._session)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is not None:
            self.rollback()
        if self._session is not None:
            self._session.close()

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("unit of work has not been entered")
        self._session.commit()

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()
