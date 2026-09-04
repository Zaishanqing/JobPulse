"""SQLAlchemy persistence schema; domain contracts remain ORM-independent."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ContractJSON = JSON().with_variant(JSONB(), "postgresql")


class PersistenceBase(DeclarativeBase):
    pass


class EvaluationTaskRow(PersistenceBase):
    __tablename__ = "evaluation_tasks"
    __table_args__ = (
        UniqueConstraint(
            "access_scope",
            "idempotency_key",
            "version_signature",
            name="uq_evaluation_tasks_idempotency",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="ck_evaluation_tasks_status",
        ),
        CheckConstraint("attempt >= 0 AND attempt <= max_attempts", name="ck_task_attempt"),
        Index("ix_evaluation_tasks_claim", "status", "lease_expires_at"),
    )

    access_scope: Mapped[str] = mapped_column(String(200), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    cv_profile_id: Mapped[str] = mapped_column(String(200), nullable=False)
    position_profile_id: Mapped[str] = mapped_column(String(200), nullable=False)
    versions_json: Mapped[dict[str, Any]] = mapped_column(ContractJSON, nullable=False)
    version_signature: Mapped[str] = mapped_column(String(2000), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluation_id: Mapped[str | None] = mapped_column(String(1024))
    error_code: Mapped[str | None] = mapped_column(String(200))
    error_message: Mapped[str | None] = mapped_column(Text)
    lease_owner: Mapped[str | None] = mapped_column(String(200))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cv_profile_json: Mapped[dict[str, Any]] = mapped_column(ContractJSON, nullable=False)
    position_profile_json: Mapped[dict[str, Any]] = mapped_column(
        ContractJSON, nullable=False
    )


class PersistedEvaluationRow(PersistenceBase):
    __tablename__ = "persisted_evaluations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["access_scope", "task_id"],
            ["evaluation_tasks.access_scope", "evaluation_tasks.task_id"],
            name="fk_persisted_evaluations_task",
            ondelete="CASCADE",
        ),
        Index("ix_persisted_evaluations_scope_stale", "access_scope", "stale"),
    )

    access_scope: Mapped[str] = mapped_column(String(200), primary_key=True)
    evaluation_id: Mapped[str] = mapped_column(String(1024), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    versions_json: Mapped[dict[str, Any]] = mapped_column(ContractJSON, nullable=False)
    version_signature: Mapped[str] = mapped_column(String(2000), nullable=False)
    evaluation_json: Mapped[dict[str, Any]] = mapped_column(ContractJSON, nullable=False)
    gap_analysis_json: Mapped[dict[str, Any]] = mapped_column(ContractJSON, nullable=False)
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stale_reason_codes: Mapped[list[str]] = mapped_column(ContractJSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditRecordRow(PersistenceBase):
    __tablename__ = "audit_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["access_scope", "task_id"],
            ["evaluation_tasks.access_scope", "evaluation_tasks.task_id"],
            name="fk_audit_records_task",
            ondelete="CASCADE",
        ),
        Index(
            "ix_audit_records_scope_task_time",
            "access_scope",
            "task_id",
            "occurred_at",
        ),
    )

    access_scope: Mapped[str] = mapped_column(String(200), primary_key=True)
    audit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str | None] = mapped_column(String(20))
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(200))
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(2000), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OutboxRecordRow(PersistenceBase):
    __tablename__ = "outbox_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["access_scope", "task_id"],
            ["evaluation_tasks.access_scope", "evaluation_tasks.task_id"],
            name="fk_outbox_records_task",
            ondelete="CASCADE",
        ),
        UniqueConstraint("access_scope", "task_id", name="uq_outbox_records_task"),
        UniqueConstraint("message_id", name="uq_outbox_records_message"),
        CheckConstraint(
            "status IN ('pending', 'claimed', 'published')",
            name="ck_outbox_records_status",
        ),
        CheckConstraint("attempt >= 0", name="ck_outbox_records_attempt"),
        Index("ix_outbox_records_dispatch", "status", "available_at", "claim_expires_at"),
    )

    outbox_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    access_scope: Mapped[str] = mapped_column(String(200), nullable=False)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    message_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(ContractJSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_by: Mapped[str | None] = mapped_column(String(200))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VectorIndexReferenceRow(PersistenceBase):
    __tablename__ = "vector_index_references"
    __table_args__ = (
        UniqueConstraint(
            "tenant_ref",
            "entity_type",
            "entity_id",
            "fragment_id",
            "profile_version",
            "embedding_revision",
            "grant_id",
            "grant_version",
            name="uq_vector_index_reference_lineage",
        ),
        CheckConstraint(
            "status IN ('pending', 'embedding', 'upserting', 'indexed', "
            "'retrying', 'failed', 'superseded', 'deleted')",
            name="ck_vector_index_references_status",
        ),
        Index(
            "ix_vector_index_references_entity",
            "tenant_ref",
            "entity_type",
            "entity_id",
            "status",
        ),
    )

    reference_id: Mapped[str] = mapped_column(String(1024), primary_key=True)
    tenant_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(200), nullable=False)
    fragment_id: Mapped[str] = mapped_column(String(200), nullable=False)
    fragment_type: Mapped[str] = mapped_column(String(80), nullable=False)
    point_id: Mapped[str] = mapped_column(String(512), nullable=False)
    profile_version: Mapped[str] = mapped_column(String(200), nullable=False)
    source_version: Mapped[str] = mapped_column(String(200), nullable=False)
    source_entity_id: Mapped[str | None] = mapped_column(String(200))
    target_type: Mapped[str | None] = mapped_column(String(80))
    grant_id: Mapped[str | None] = mapped_column(String(200))
    grant_version: Mapped[int | None] = mapped_column()
    personal_tenant_ref: Mapped[str | None] = mapped_column(String(200))
    enterprise_tenant_ref: Mapped[str | None] = mapped_column(String(200))
    embedding_model: Mapped[str] = mapped_column(String(200), nullable=False)
    embedding_revision: Mapped[str] = mapped_column(String(200), nullable=False)
    vector_schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(200))
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VectorOutboxEventRow(PersistenceBase):
    __tablename__ = "vector_outbox_events"
    __table_args__ = (
        UniqueConstraint("deduplication_key", name="uq_vector_outbox_deduplication"),
        CheckConstraint(
            "status IN ('pending', 'claimed', 'retrying', 'processed', 'dead_letter')",
            name="ck_vector_outbox_events_status",
        ),
        CheckConstraint(
            "attempt >= 0 AND attempt <= max_attempts",
            name="ck_vector_outbox_events_attempt",
        ),
        Index(
            "ix_vector_outbox_events_claim",
            "status",
            "available_at",
            "claim_expires_at",
        ),
    )

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    deduplication_key: Mapped[str] = mapped_column(String(700), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(ContractJSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_by: Mapped[str | None] = mapped_column(String(200))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_reference_ids: Mapped[list[str]] = mapped_column(
        ContractJSON, nullable=False
    )
    last_error_code: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VectorOutboxAuditRow(PersistenceBase):
    __tablename__ = "vector_outbox_audits"
    __table_args__ = (
        ForeignKeyConstraint(
            ["event_id"],
            ["vector_outbox_events.event_id"],
            name="fk_vector_outbox_audits_event",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "event_id", "sequence", name="uq_vector_outbox_audits_sequence"
        ),
        Index(
            "ix_vector_outbox_audits_event_time", "event_id", "occurred_at"
        ),
    )

    audit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(200))
    correlation_id: Mapped[str] = mapped_column(String(200), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
