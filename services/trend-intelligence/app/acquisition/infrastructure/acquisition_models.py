from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AcquisitionSourceModel(Base):
    __tablename__ = "acquisition_sources"
    __table_args__ = (
        CheckConstraint("status IN ('active','inactive','deprecated')", name="ck_acquisition_source_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    endpoint_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    auth_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    rate_limit_rps: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    compliance_policy: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)


class CrawlJobModel(Base):
    __tablename__ = "crawl_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','succeeded','failed','cancelled')",
            name="ck_crawl_job_status",
        ),
        Index("ix_crawl_jobs_claim", "status", "available_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_id: Mapped[str] = mapped_column(
        ForeignKey("acquisition_sources.id"), nullable=False, index=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    rate_limit_rps: Mapped[float | None] = mapped_column(Float, nullable=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_snapshot_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RawSnapshotModel(Base):
    __tablename__ = "raw_snapshots"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", "source_version", name="uq_raw_snapshot_identity"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    job_id: Mapped[str] = mapped_column(ForeignKey("crawl_jobs.id"), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("acquisition_sources.id"), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(256), nullable=False)
    raw_content: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_version: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    snapshot_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class RawSnapshotObservationModel(Base):
    __tablename__ = "raw_snapshot_observations"

    job_id: Mapped[str] = mapped_column(
        ForeignKey("crawl_jobs.id"), primary_key=True,
    )
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("raw_snapshots.id"), primary_key=True,
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now,
    )


class AcquisitionBundleModel(Base):
    __tablename__ = "acquisition_bundles"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','ready','imported','failed')",
            name="ck_acquisition_bundle_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    job_id: Mapped[str] = mapped_column(ForeignKey("crawl_jobs.id"), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("acquisition_sources.id"), nullable=False, index=True)
    bundle_type: Mapped[str] = mapped_column(String(32), nullable=False)
    snapshot_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    analysis_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_runs.id", name="fk_acquisition_bundles_analysis_run_id"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)


class AcquisitionOutboxModel(Base):
    __tablename__ = "acquisition_outbox"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','processing','processed','failed')",
            name="ck_acquisition_outbox_status",
        ),
        Index("ix_acquisition_outbox_claim", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
