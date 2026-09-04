from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Boolean,
    JSON,
    String,
    Text,
    UniqueConstraint,
    event,
)

from app.core.database import Base
from app.models.user import utc_now


class SourceCV(Base):
    __tablename__ = "source_cvs"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "source_platform",
            "source_record_id",
            name="uq_source_cvs_owner_source_identity",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id = Column(String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    source_platform = Column(String(64), nullable=False)
    source_record_id = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class SourceCVVersion(Base):
    __tablename__ = "source_cv_versions"
    __table_args__ = (
        UniqueConstraint("source_cv_id", "source_version", name="uq_source_cv_versions_version"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_cv_id = Column(
        String(36), ForeignKey("source_cvs.id", ondelete="RESTRICT"), nullable=False
    )
    source_file_id = Column(
        String(36),
        ForeignKey(
            "file_assets.id",
            ondelete="RESTRICT",
            name="fk_source_cv_versions_source_file_id",
        ),
        nullable=True,
    )
    original_filename = Column(String(255), nullable=True)
    content_type = Column(String(128), nullable=True)
    extraction_method = Column(String(32), nullable=True)
    extraction_provider = Column(String(64), nullable=True)
    extraction_provider_version = Column(String(64), nullable=True)
    text_extraction_status = Column(String(16), nullable=True)
    page_count = Column(Integer, nullable=True)
    quality_flags = Column(JSON, nullable=True)
    ocr_layout = Column(JSON, nullable=True)
    raw_text = Column(Text, nullable=False)
    source_version = Column(String(64), nullable=False, default="1")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class CVExtractionTask(Base):
    __tablename__ = "cv_extraction_tasks"
    __table_args__ = (
        UniqueConstraint(
            "source_cv_version_id",
            "request_id",
            name="uq_cv_extraction_tasks_natural_key",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_cv_extraction_tasks_status",
        ),
        CheckConstraint(
            "validation_conclusion IS NULL OR validation_conclusion IN ('pass', 'warn', 'block')",
            name="ck_cv_extraction_tasks_conclusion",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_cv_extraction_tasks_attempts"),
        CheckConstraint(
            "max_attempts > 0 AND attempt_count <= max_attempts",
            name="ck_cv_extraction_tasks_attempt_limit",
        ),
        CheckConstraint(
            "confirmation_status IS NULL OR confirmation_status IN ('pending', 'confirmed')",
            name="ck_cv_extraction_tasks_confirmation_status",
        ),
        Index(
            "ix_cv_extraction_tasks_claim_queue",
            "status",
            "retryable",
            "next_attempt_at",
            "created_at",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_cv_version_id = Column(
        String(36),
        ForeignKey("source_cv_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    request_id = Column(String(128), nullable=False)
    execution_id = Column(String(128), nullable=True)
    execution_metadata = Column(JSON, nullable=True)
    status = Column(String(16), nullable=False, default="pending", index=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    last_error_code = Column(String(96), nullable=True)
    last_error_message = Column(String(512), nullable=True)
    retryable = Column(Boolean, nullable=False, default=False)
    claimed_by = Column(String(120), nullable=True, index=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True, index=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    validation_conclusion = Column(String(16), nullable=True)
    validation_report_payload = Column(JSON, nullable=True)
    resume_id = Column(String(36), ForeignKey("resumes.id", ondelete="RESTRICT"), nullable=True)
    review_payload = Column(JSON, nullable=True)
    review_id = Column(String(128), nullable=True)
    confirmation_status = Column(String(16), nullable=True)
    latest_validated_cv_snapshot_id = Column(
        String(36),
        ForeignKey(
            "validated_cv_snapshots.id",
            ondelete="RESTRICT",
            name="fk_cv_extraction_tasks_latest_snapshot",
        ),
        nullable=True,
    )
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    confirmed_by = Column(String(36), nullable=True)
    review_revision = Column(Integer, nullable=False, default=0)
    confirmation_idempotency_key = Column(String(128), nullable=True)
    confirmation_idempotency_id = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class ValidatedCVSnapshot(Base):
    __tablename__ = "validated_cv_snapshots"
    __table_args__ = (
        CheckConstraint(
            "conclusion IN ('pass', 'warn')",
            name="ck_validated_cv_snapshots_conclusion",
        ),
        Index("ix_validated_cv_snapshots_task", "cv_extraction_task_id"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    cv_extraction_task_id = Column(
        String(36),
        ForeignKey("cv_extraction_tasks.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_file_id = Column(String(36), nullable=True)
    snapshot_revision = Column(Integer, nullable=False, default=1)
    supersedes_snapshot_id = Column(
        String(36),
        ForeignKey("validated_cv_snapshots.id", ondelete="RESTRICT"),
        nullable=True,
    )
    confirmed_by = Column(String(36), nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    extraction_provider = Column(String(64), nullable=True)
    model = Column(String(64), nullable=True)
    prompt_version = Column(String(64), nullable=True)
    extraction_schema_version = Column(String(64), nullable=True)
    normalization_version = Column(String(64), nullable=True)
    taxonomy_version = Column(String(71), nullable=True)
    field_decisions = Column(JSON, nullable=True)
    evidence_payload = Column(JSON, nullable=True)
    source_cv_version_id = Column(
        String(36),
        ForeignKey("source_cv_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    validation_report_id = Column(
        String(36),
        ForeignKey("cv_validation_reports.id", ondelete="RESTRICT"),
        nullable=False,
    )
    policy_version = Column(String(64), nullable=False)
    conclusion = Column(String(16), nullable=False)
    extraction_payload = Column(JSON, nullable=False)
    normalized_payload = Column(JSON, nullable=False)
    findings_payload = Column(JSON, nullable=False)
    execution_metadata = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


def _reject_immutable_mutation(mapper, connection, target) -> None:
    raise ValueError(f"{type(target).__name__} records are immutable")


for immutable_type in (SourceCVVersion, ValidatedCVSnapshot):
    event.listen(immutable_type, "before_update", _reject_immutable_mutation)
    event.listen(immutable_type, "before_delete", _reject_immutable_mutation)
