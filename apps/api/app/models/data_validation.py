from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    event,
)

from app.core.database import Base
from app.models.user import utc_now


class DataValidationTask(Base):
    __tablename__ = "data_validation_tasks"
    __table_args__ = (
        UniqueConstraint(
            "extraction_task_id",
            "bundle_id",
            "policy_version",
            name="uq_data_validation_tasks_natural_key",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_data_validation_tasks_idempotency_key",
        ),
        CheckConstraint(
            "status in ('pending', 'running', 'succeeded', 'failed')",
            name="ck_data_validation_tasks_status_allowed",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_data_validation_tasks_attempt_nonnegative",
        ),
        CheckConstraint(
            "max_attempts > 0",
            name="ck_data_validation_tasks_max_attempts_positive",
        ),
        CheckConstraint(
            "attempt_count <= max_attempts",
            name="ck_data_validation_tasks_attempt_within_max",
        ),
        CheckConstraint(
            "lock_version >= 1",
            name="ck_data_validation_tasks_lock_version_positive",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    extraction_task_id = Column(
        String(36),
        ForeignKey("extraction_tasks.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_jd_version_id = Column(
        String(36),
        ForeignKey("source_jd_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    bundle_id = Column(String(71), nullable=False)
    policy_version = Column(String(64), nullable=False)
    idempotency_key = Column(String(180), nullable=False)
    status = Column(String(24), nullable=False, default="pending", index=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    last_error_code = Column(String(80), nullable=True)
    last_error_message = Column(Text, nullable=True)
    retryable = Column(Boolean, nullable=False, default=False)
    lock_version = Column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


class ValidationReport(Base):
    __tablename__ = "validation_reports"
    __table_args__ = (
        UniqueConstraint(
            "data_validation_task_id",
            name="uq_validation_reports_task_id",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_validation_reports_idempotency_key",
        ),
        CheckConstraint(
            "conclusion in ('pass', 'warn', 'block')",
            name="ck_validation_reports_conclusion_allowed",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    data_validation_task_id = Column(
        String(36),
        ForeignKey("data_validation_tasks.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    conclusion = Column(String(16), nullable=False, index=True)
    idempotency_key = Column(String(180), nullable=False)
    policy_version = Column(String(64), nullable=False)
    report_payload = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class CVDataValidationTask(Base):
    __tablename__ = "cv_data_validation_tasks"
    __table_args__ = (
        UniqueConstraint(
            "cv_extraction_task_id",
            "policy_version",
            name="uq_cv_data_validation_tasks_natural_key",
        ),
        CheckConstraint(
            "status in ('succeeded', 'failed')",
            name="ck_cv_data_validation_tasks_status_allowed",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    cv_extraction_task_id = Column(
        String(36),
        ForeignKey("cv_extraction_tasks.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_cv_version_id = Column(
        String(36),
        ForeignKey("source_cv_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    policy_version = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    finished_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class CVValidationReport(Base):
    __tablename__ = "cv_validation_reports"
    __table_args__ = (
        UniqueConstraint(
            "cv_data_validation_task_id",
            name="uq_cv_validation_reports_task_id",
        ),
        CheckConstraint(
            "conclusion in ('pass', 'warn', 'block')",
            name="ck_cv_validation_reports_conclusion_allowed",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    cv_data_validation_task_id = Column(
        String(36),
        ForeignKey("cv_data_validation_tasks.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    conclusion = Column(String(16), nullable=False, index=True)
    policy_version = Column(String(64), nullable=False)
    report_payload = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class ValidatedBundleSnapshot(Base):
    __tablename__ = "validated_bundle_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "validation_report_id",
            name="uq_validated_bundle_snapshots_report_id",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_validated_bundle_snapshots_idempotency_key",
        ),
        CheckConstraint(
            "validation_conclusion in ('pass', 'warn')",
            name="ck_validated_bundle_snapshots_non_blocking",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    validation_report_id = Column(
        String(36),
        ForeignKey("validation_reports.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    data_validation_task_id = Column(
        String(36),
        ForeignKey("data_validation_tasks.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    extraction_task_id = Column(
        String(36),
        ForeignKey("extraction_tasks.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_jd_version_id = Column(
        String(36),
        ForeignKey("source_jd_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    validation_conclusion = Column(String(16), nullable=False)
    bundle_id = Column(String(71), nullable=False)
    idempotency_key = Column(String(180), nullable=False)
    bundle_payload = Column(JSON, nullable=False)
    report_payload = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class ValidatedFactHash(Base):
    __tablename__ = "validated_fact_hashes"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "canonical_hash",
            name="uq_validated_fact_hashes_snapshot_hash",
        ),
    )

    id = Column(String(36), primary_key=True)
    snapshot_id = Column(
        String(36),
        ForeignKey("validated_bundle_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_jd_version_id = Column(
        String(36),
        ForeignKey("source_jd_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    canonical_hash = Column(String(71), nullable=False, index=True)


def _reject_snapshot_mutation(mapper, connection, target) -> None:
    raise ValueError("ValidatedBundleSnapshot records are immutable")


event.listen(ValidatedBundleSnapshot, "before_update", _reject_snapshot_mutation)
event.listen(ValidatedBundleSnapshot, "before_delete", _reject_snapshot_mutation)
