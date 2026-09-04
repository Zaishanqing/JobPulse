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
)

from app.core.database import Base
from app.models.user import utc_now


EXTRACTION_TASK_STATUSES = ("pending", "running", "succeeded", "failed")
EXTRACTION_MODES = ("llm", "rule")


class ExtractionTask(Base):
    __tablename__ = "extraction_tasks"
    __table_args__ = (
        UniqueConstraint(
            "source_jd_version_id",
            "request_id",
            name="uq_extraction_tasks_version_request",
        ),
        CheckConstraint(
            f"status in {EXTRACTION_TASK_STATUSES}",
            name="ck_extraction_tasks_status_allowed",
        ),
        CheckConstraint(
            f"extraction_mode in {EXTRACTION_MODES}",
            name="ck_extraction_tasks_mode_allowed",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_extraction_tasks_attempt_nonnegative"),
        CheckConstraint("max_attempts > 0", name="ck_extraction_tasks_max_attempts_positive"),
        CheckConstraint(
            "attempt_count <= max_attempts",
            name="ck_extraction_tasks_attempt_within_max",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_jd_version_id = Column(
        String(36),
        ForeignKey("source_jd_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status = Column(String(24), nullable=False, default="pending", index=True)
    extraction_mode = Column(String(16), nullable=False)
    provider = Column(String(80), nullable=False)
    request_id = Column(String(128), nullable=False)
    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    last_error_code = Column(String(80), nullable=True)
    last_error_message = Column(Text, nullable=True)
    retryable = Column(Boolean, nullable=False, default=False)
    bundle_payload = Column(JSON, nullable=True)
    claimed_by = Column(String(120), nullable=True, index=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
