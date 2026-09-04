from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


CVExtractionTaskStatus = Literal["pending", "running", "succeeded", "failed"]
CVProcessingStage = Literal[
    "queued",
    "ocr_running",
    "extracting",
    "contract_validating",
    "semantic_repairing",
    "review_pending",
    "failed",
    "succeeded",
]
CVConfirmationStatus = Literal["pending", "confirmed"]
CVValidationConclusion = Literal["pass", "warn", "block"]


class CVResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceCVImportResponse(CVResponseModel):
    source_cv_id: str
    source_cv_version_id: str
    cv_extraction_task_id: str
    created_source: bool
    created_version: bool
    created_task: bool
    task_status: CVExtractionTaskStatus
    text_extraction_status: str | None = None
    extraction_method: str | None = None
    extraction_provider: str | None = None
    source_file_id: str | None = None


class CVExtractionTaskResponse(CVResponseModel):
    task_id: str
    source_cv_version_id: str
    owner_id: str
    request_id: str
    execution_id: str | None = None
    execution_metadata: dict[str, Any] | None = None
    status: CVExtractionTaskStatus
    processing_stage: CVProcessingStage
    attempt_count: int = Field(ge=0)
    max_attempts: int = Field(gt=0)
    last_error_code: str | None = None
    last_error_message: str | None = None
    retryable: bool = False
    claimed_by: str | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    next_attempt_at: datetime | None = None
    finished_at: datetime | None = None
    validation_conclusion: CVValidationConclusion | None = None
    validation_report_payload: dict[str, Any] | None = None
    validation_task_id: str | None = None
    validation_report_id: str | None = None
    resume_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    review_payload: dict[str, Any] | None = None
    review_id: str | None = None
    confirmation_status: CVConfirmationStatus | None = None
    latest_validated_cv_snapshot_id: str | None = None
    confirmed_at: datetime | None = None
    confirmed_by: str | None = None
    review_revision: int = 0
    confirmation_idempotency_key: str | None = None
    confirmation_idempotency_id: str | None = None


class CVConfirmationResponse(CVResponseModel):
    snapshot_id: str
    snapshot_revision: int = Field(ge=1)
    resume_id: str
    task_id: str
    supersedes_snapshot_id: str | None = None
    idempotency_key: str | None = None


class ValidatedCVSnapshotResponse(CVResponseModel):
    snapshot_id: str
    cv_extraction_task_id: str
    source_cv_version_id: str
    validation_report_id: str
    policy_version: str
    conclusion: Literal["pass", "warn"]
    extraction_payload: dict[str, Any]
    normalized_payload: dict[str, Any]
    findings_payload: dict[str, Any]
    execution_metadata: dict[str, Any]
    created_at: datetime | None = None
    source_file_id: str | None = None
    snapshot_revision: int = Field(default=1, ge=1)
    supersedes_snapshot_id: str | None = None
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    extraction_provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    extraction_schema_version: str | None = None
    normalization_version: str | None = None
    taxonomy_version: str | None = None
    field_decisions: list[dict[str, Any]] | None = None
    evidence_payload: dict[str, Any] | None = None
    resume_id: str | None = None


class CVReviewEvidenceResponse(CVResponseModel):
    source_document_id: str
    source_id: str
    quote: str
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)
    alignment: str = "unresolved"
    occurrence_index: int | None = Field(default=None, ge=0)


class CVReviewableFieldResponse(CVResponseModel):
    field_id: str
    field_type: str
    section: str
    item_id: str
    field_path: str
    field_label: str
    original_value: str | None = None
    suggested_value: str | None = None
    evidence: CVReviewEvidenceResponse | None = None
    flag_codes: list[str] = Field(default_factory=list)


class CVReviewFlagResponse(CVResponseModel):
    code: str
    severity: str
    rule_scope: str | None = None
    message: str | None = None
    suggested_action: str | None = None
    item_id: str | None = None


class CVValidationSummaryResponse(CVResponseModel):
    conclusion: CVValidationConclusion
    policy_version: str
    validation_task_id: str | None = None
    validation_report_id: str | None = None
    blocking_reasons: list[str] = Field(default_factory=list)


class CVReviewResponse(CVResponseModel):
    task_id: str
    source_cv_id: str
    source_cv_version_id: str
    status: CVExtractionTaskStatus
    confirmation_status: CVConfirmationStatus | None = None
    review_id: str | None = None
    review_revision: int = 0
    source_text: str | None = None
    source_file_id: str | None = None
    content_type: str | None = None
    ocr_layout: list[dict[str, Any]] | None = None
    reviewable_fields: list[CVReviewableFieldResponse] = Field(default_factory=list)
    review_flags: list[CVReviewFlagResponse] = Field(default_factory=list)
    validation: CVValidationSummaryResponse | None = None


class CVEnvelope(CVResponseModel):
    code: Literal[0] = 0
    message: Literal["success"] = "success"
    trace_id: str


class SourceCVImportEnvelope(CVEnvelope):
    data: SourceCVImportResponse


class CVExtractionTaskEnvelope(CVEnvelope):
    data: CVExtractionTaskResponse


class CVConfirmationEnvelope(CVEnvelope):
    data: CVConfirmationResponse


class ValidatedCVSnapshotEnvelope(CVEnvelope):
    data: ValidatedCVSnapshotResponse


class CVReviewEnvelope(CVEnvelope):
    data: CVReviewResponse
