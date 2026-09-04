from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.domain.json_types import FrozenJsonObject


CV_VALIDATION_POLICY_VERSION = "cv-validation-policy.v2"

CV_EXTRACTION_METHODS = frozenset(
    {"pdf_text", "docx_text", "ocr_image", "ocr_pdf", "direct_text"}
)
CV_EXTRACTION_STATUSES = frozenset({"completed", "review_required", "failed"})
CV_QUALITY_FLAG_NONE = "none"
CV_QUALITY_FLAG_OCR = "ocr_text"
CV_QUALITY_FLAG_REVIEW_REQUIRED = "review_required"
CV_QUALITY_FLAG_DIRECT_TEXT = "direct_text_dev_only"


@dataclass(frozen=True)
class SourceCVImportResult:
    source_cv_id: str
    source_cv_version_id: str
    cv_extraction_task_id: str
    created_source: bool
    created_version: bool
    created_task: bool
    task_status: str
    text_extraction_status: str | None = None
    extraction_method: str | None = None
    extraction_provider: str | None = None
    source_file_id: str | None = None


@dataclass(frozen=True)
class CVDocumentTextExtraction:
    source_file_id: str
    original_filename: str
    content_type: str
    extraction_method: str
    extraction_provider: str
    extraction_provider_version: str | None
    extraction_status: Literal["completed", "review_required", "failed"]
    raw_text: str
    page_count: int
    character_count: int
    quality_flags: tuple[str, ...] = (CV_QUALITY_FLAG_NONE,)
    ocr_layout: tuple[FrozenJsonObject, ...] | None = None
    error_code: str | None = None
    error_message: str | None = None


class CVFileInputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CVExtractionTaskRecord:
    task_id: str
    source_cv_version_id: str
    owner_id: str
    request_id: str
    execution_id: str | None
    execution_metadata: FrozenJsonObject | None
    status: str
    attempt_count: int
    max_attempts: int
    last_error_code: str | None
    last_error_message: str | None
    retryable: bool
    claimed_by: str | None
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    next_attempt_at: datetime | None
    finished_at: datetime | None
    validation_conclusion: str | None
    validation_report_payload: FrozenJsonObject | None
    validation_task_id: str | None
    validation_report_id: str | None
    resume_id: str | None
    created_at: datetime
    updated_at: datetime
    review_payload: FrozenJsonObject | None = None
    review_id: str | None = None
    confirmation_status: str | None = None
    latest_validated_cv_snapshot_id: str | None = None
    confirmed_at: datetime | None = None
    confirmed_by: str | None = None
    review_revision: int = 0
    confirmation_idempotency_key: str | None = None
    confirmation_idempotency_id: str | None = None


@dataclass(frozen=True)
class SourceCVVersionRecord:
    version_id: str
    source_cv_id: str
    owner_id: str
    raw_text: str
    source_version: str
    created_at: datetime
    source_file_id: str | None = None
    original_filename: str | None = None
    content_type: str | None = None
    extraction_method: str | None = None
    extraction_provider: str | None = None
    extraction_provider_version: str | None = None
    text_extraction_status: str | None = None
    page_count: int | None = None
    quality_flags: tuple[str, ...] | None = None
    ocr_layout: tuple[FrozenJsonObject, ...] | None = None


@dataclass(frozen=True)
class ValidatedCVSnapshotRecord:
    snapshot_id: str
    cv_extraction_task_id: str
    source_cv_version_id: str
    validation_report_id: str
    policy_version: str
    conclusion: str
    extraction_payload: FrozenJsonObject
    normalized_payload: FrozenJsonObject
    findings_payload: FrozenJsonObject
    execution_metadata: FrozenJsonObject
    created_at: datetime
    source_file_id: str | None = None
    snapshot_revision: int = 1
    supersedes_snapshot_id: str | None = None
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    extraction_provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    extraction_schema_version: str | None = None
    normalization_version: str | None = None
    taxonomy_version: str | None = None
    field_decisions: tuple[FrozenJsonObject, ...] | None = None
    evidence_payload: FrozenJsonObject | None = None
    resume_id: str | None = None


@dataclass(frozen=True)
class CVFieldDecision:
    field_id: str
    field_type: str
    section: str
    decision: Literal["accept", "correct", "unknown", "remove"]
    item_id: str | None = None
    field_path: str | None = None
    corrected_value: str | None = None
    correction_reason: str | None = None
    evidence_quote: str | None = None
    evidence_start: int | None = None
    evidence_end: int | None = None


@dataclass(frozen=True)
class CVReviewConfirmation:
    expected_review_id: str
    idempotency_key: str
    field_decisions: tuple[CVFieldDecision, ...] = ()
    normalization_version: str | None = None
    taxonomy_version: str | None = None
    display_name: str | None = None


@dataclass(frozen=True)
class CVConfirmationResult:
    snapshot_id: str
    snapshot_revision: int
    resume_id: str
    task_id: str
    supersedes_snapshot_id: str | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True)
class CVReviewResult:
    task: CVExtractionTaskRecord
    source_cv_id: str
    source_cv_version_id: str
    source_text: str
    source_file_id: str | None = None
    content_type: str | None = None
    ocr_layout: tuple[FrozenJsonObject, ...] | None = None
