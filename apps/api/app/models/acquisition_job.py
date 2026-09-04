from __future__ import annotations

from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)

from app.core.database import Base
from app.models.user import utc_now


class AcquisitionJob(Base):
    __tablename__ = "acquisition_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ("
            "'pending', 'crawling', 'exporting', 'verifying', 'importing', "
            "'completed', 'crawl_failed', 'export_failed', 'verify_failed', "
            "'import_failed', 'cancelled'"
            ")",
            name="ck_acquisition_jobs_status_allowed",
        ),
        CheckConstraint("pages > 0", name="ck_acquisition_jobs_pages_positive"),
        CheckConstraint(
            "progress >= 0 AND progress <= 1",
            name="ck_acquisition_jobs_progress_range",
        ),
        CheckConstraint(
            "attempt >= 1",
            name="ck_acquisition_jobs_attempt_positive",
        ),
        CheckConstraint(
            "discovered_count >= 0 AND exported_count >= 0 "
            "AND imported_count >= 0 AND no_op_count >= 0 AND failed_count >= 0",
            name="ck_acquisition_jobs_counts_nonnegative",
        ),
        CheckConstraint(
            "imported_count + no_op_count + failed_count <= exported_count",
            name="ck_acquisition_jobs_counts_total",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    requested_by = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    source = Column(String(32), nullable=False, index=True)
    keyword = Column(String(255), nullable=False)
    city = Column(String(64), nullable=False)
    pages = Column(Integer, nullable=False, default=5)
    status = Column(String(32), nullable=False, default="pending", index=True)
    progress = Column(Float, nullable=False, default=0.0)
    crawler_task_id = Column(String(64), nullable=True)
    bundle_id = Column(String(128), nullable=True, index=True)
    bundle_file_name = Column(String(255), nullable=True)
    bundle_hash = Column(String(128), nullable=True)
    discovered_count = Column(Integer, nullable=False, default=0)
    exported_count = Column(Integer, nullable=False, default=0)
    imported_count = Column(Integer, nullable=False, default=0)
    no_op_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    import_batch_id = Column(String(36), nullable=True)
    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    retry_of_id = Column(String(36), nullable=True, index=True)
    attempt = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
