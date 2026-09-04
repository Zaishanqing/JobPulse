from __future__ import annotations

from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from app.core.database import Base
from app.models.user import utc_now


class OfflineImportBatch(Base):
    __tablename__ = "offline_import_batches"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('incremental', 'full')",
            name="ck_offline_import_batches_mode",
        ),
        CheckConstraint(
            "status IN ('pending', 'importing', 'completed', 'completed_with_errors', 'failed')",
            name="ck_offline_import_batches_status",
        ),
        CheckConstraint(
            "record_count >= 0 AND imported_count >= 0 "
            "AND skipped_count >= 0 AND failed_count >= 0",
            name="ck_offline_import_batches_counts_nonnegative",
        ),
        CheckConstraint(
            "imported_count + skipped_count + failed_count <= record_count",
            name="ck_offline_import_batches_counts_total",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    bundle_id = Column(String(128), nullable=False, unique=True)
    # Nullable only for batches created before immutable bundle identity was
    # introduced. Such legacy rows must not receive an automatic no-op.
    bundle_digest = Column(String(64), nullable=True)
    bundle_schema_version = Column(String(64), nullable=False)
    record_schema_version = Column(String(64), nullable=False)
    mode = Column(String(32), nullable=False)
    parent_bundle_id = Column(String(128), nullable=True, index=True)
    file_name = Column(String(255), nullable=False)
    record_count = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="pending")
    imported_count = Column(Integer, nullable=False, default=0)
    skipped_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    started_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)


class OfflineImportItem(Base):
    __tablename__ = "offline_import_items"
    __table_args__ = (
        UniqueConstraint("batch_id", "line_number", name="uq_offline_import_items_batch_line"),
        CheckConstraint("line_number > 0", name="ck_offline_import_items_line_number"),
        CheckConstraint(
            "status IN ('pending', 'imported', 'skipped', 'failed')",
            name="ck_offline_import_items_status",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    batch_id = Column(
        String(36),
        ForeignKey("offline_import_batches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    line_number = Column(Integer, nullable=False)
    source_platform = Column(String(64), nullable=True)
    source_record_id = Column(String(255), nullable=True)
    source_version = Column(String(128), nullable=True)
    status = Column(String(32), nullable=False, default="pending")
    source_jd_id = Column(String(36), nullable=True)
    source_jd_version_id = Column(String(36), nullable=True)
    extraction_task_id = Column(String(36), nullable=True)
    error_code = Column(String(128), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
