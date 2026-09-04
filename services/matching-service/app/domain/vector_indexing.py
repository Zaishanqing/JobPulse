"""Persistent vector-index references and transactional outbox contracts."""

from __future__ import annotations

import hashlib
from uuid import uuid4
from datetime import datetime, timezone
from typing import Literal

from pydantic import Field, model_validator

from app.domain.profiles import ImmutableDTO

VectorEntityType = Literal["cv", "position"]
VectorReferenceStatus = Literal[
    "pending",
    "embedding",
    "upserting",
    "indexed",
    "retrying",
    "failed",
    "superseded",
    "deleted",
]
VectorEventType = Literal[
    "cv_profile_published",
    "cv_profile_updated",
    "cv_profile_revoked",
    "position_profile_published",
    "position_profile_updated",
    "position_profile_revoked",
    "embedding_revision_changed",
    "vector_reindex_requested",
]
VectorOutboxStatus = Literal["pending", "claimed", "retrying", "processed", "dead_letter"]


class VectorIndexReferenceRecord(ImmutableDTO):
    reference_id: str = Field(min_length=1, max_length=1024)
    tenant_ref: str = Field(min_length=1, max_length=200)
    entity_type: VectorEntityType
    entity_id: str = Field(min_length=1, max_length=200)
    fragment_id: str = Field(min_length=1, max_length=200)
    fragment_type: str = Field(min_length=1, max_length=80)
    point_id: str = Field(min_length=1, max_length=512)
    profile_version: str = Field(min_length=1, max_length=200)
    source_version: str = Field(min_length=1, max_length=200)
    source_entity_id: str | None = Field(default=None, min_length=1, max_length=200)
    target_type: str | None = Field(default=None, min_length=1, max_length=80)
    grant_id: str | None = Field(default=None, min_length=1, max_length=200)
    grant_version: int | None = Field(default=None, ge=1)
    personal_tenant_ref: str | None = Field(default=None, min_length=1, max_length=200)
    enterprise_tenant_ref: str | None = Field(default=None, min_length=1, max_length=200)
    embedding_model: str = Field(min_length=1, max_length=200)
    embedding_revision: str = Field(min_length=1, max_length=200)
    vector_schema_version: str = Field(min_length=1, max_length=80)
    status: VectorReferenceStatus = "pending"
    error_code: str | None = Field(default=None, max_length=200)
    indexed_at: datetime | None = None
    superseded_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_state_times(self) -> VectorIndexReferenceRecord:
        if self.status == "indexed" and self.indexed_at is None:
            raise ValueError("indexed reference requires indexed_at")
        if self.status == "superseded" and self.superseded_at is None:
            raise ValueError("superseded reference requires superseded_at")
        return self


def vector_reference_id(
    *,
    tenant_ref: str,
    entity_type: VectorEntityType,
    entity_id: str,
    fragment_id: str,
    profile_version: str,
    embedding_revision: str,
    grant_id: str | None = None,
    grant_version: int | None = None,
) -> str:
    return ":".join(
        (
            "vref",
            tenant_ref,
            entity_type,
            entity_id,
            fragment_id,
            profile_version,
            embedding_revision,
        )
    )


class VectorOutboxPayload(ImmutableDTO):
    entity_type: VectorEntityType
    entity_id: str = Field(min_length=1, max_length=200)
    tenant_ref: str = Field(min_length=1, max_length=200)
    profile_version: str = Field(min_length=1, max_length=200)
    source_version: str = Field(min_length=1, max_length=200)
    source_entity_id: str | None = Field(default=None, min_length=1, max_length=200)
    target_type: str | None = Field(default=None, min_length=1, max_length=80)
    grant_id: str | None = Field(default=None, min_length=1, max_length=200)
    grant_version: int | None = Field(default=None, ge=1)
    personal_tenant_ref: str | None = Field(default=None, min_length=1, max_length=200)
    enterprise_tenant_ref: str | None = Field(default=None, min_length=1, max_length=200)
    requested_embedding_revision: str = Field(min_length=1, max_length=200)
    correlation_id: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_projection_lineage(self):
        projection = (
            self.grant_id,
            self.grant_version,
            self.personal_tenant_ref,
            self.enterprise_tenant_ref,
        )
        if any(value is not None for value in projection) and any(
            value is None for value in projection
        ):
            raise ValueError("enterprise projection lineage must be complete")
        if (
            self.enterprise_tenant_ref is not None
            and self.enterprise_tenant_ref != self.tenant_ref
        ):
            raise ValueError("enterprise projection tenant must match event tenant")
        return self


def vector_event_deduplication_key(
    event_type: VectorEventType, payload: VectorOutboxPayload
) -> str:
    values = payload.model_dump(
        mode="json",
        exclude=set() if event_type == "vector_reindex_requested" else {"correlation_id"},
    )
    return "|".join((event_type, *(f"{key}={values[key]}" for key in sorted(values))))


class VectorOutboxEvent(ImmutableDTO):
    event_id: str = Field(min_length=1, max_length=64)
    event_type: VectorEventType
    deduplication_key: str = Field(min_length=1, max_length=700)
    payload: VectorOutboxPayload
    status: VectorOutboxStatus = "pending"
    attempt: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=5, ge=1)
    available_at: datetime
    claimed_by: str | None = Field(default=None, min_length=1, max_length=200)
    claim_expires_at: datetime | None = None
    processed_at: datetime | None = None
    acknowledged_reference_ids: tuple[str, ...] = ()
    last_error_code: str | None = Field(default=None, max_length=200)
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create(
        cls,
        *,
        event_type: VectorEventType,
        payload: VectorOutboxPayload,
        max_attempts: int = 5,
        now: datetime | None = None,
    ) -> VectorOutboxEvent:
        occurred_at = now or datetime.now(timezone.utc)
        deduplication_key = vector_event_deduplication_key(event_type, payload)
        return cls(
            event_id="vevt_" + hashlib.sha256(deduplication_key.encode("utf-8")).hexdigest()[:32],
            event_type=event_type,
            deduplication_key=deduplication_key,
            payload=payload,
            max_attempts=max_attempts,
            available_at=occurred_at,
            created_at=occurred_at,
            updated_at=occurred_at,
        )

    @model_validator(mode="after")
    def validate_event_state(self) -> VectorOutboxEvent:
        expected = vector_event_deduplication_key(self.event_type, self.payload)
        if self.deduplication_key != expected:
            raise ValueError("vector event deduplication key does not match payload")
        if self.status == "processed" and self.processed_at is None:
            raise ValueError("processed event requires processed_at")
        if self.status == "dead_letter" and not self.last_error_code:
            raise ValueError("dead-letter event requires an error code")
        return self


class VectorOutboxClaim(ImmutableDTO):
    event: VectorOutboxEvent
    from_status: Literal["pending", "retrying", "claimed"]


class VectorOutboxAuditRecord(ImmutableDTO):
    audit_id: str = Field(min_length=1, max_length=64)
    event_id: str = Field(min_length=1, max_length=64)
    event_type: VectorEventType
    sequence: int = Field(ge=0)
    from_status: VectorOutboxStatus | None = None
    to_status: VectorOutboxStatus
    attempt: int = Field(ge=0)
    reason_code: str | None = Field(default=None, max_length=200)
    correlation_id: str = Field(min_length=1, max_length=200)
    occurred_at: datetime


def vector_outbox_audit(
    event: VectorOutboxEvent,
    *,
    from_status: VectorOutboxStatus | None,
    to_status: VectorOutboxStatus,
    occurred_at: datetime,
    reason_code: str | None = None,
    sequence_override: int | None = None,
) -> VectorOutboxAuditRecord:
    if sequence_override is not None and sequence_override < 0:
        raise ValueError("vector audit sequence cannot be negative")
    sequence = sequence_override
    if sequence is None:
        sequence = 0 if from_status is None and to_status == "pending" else event.attempt * 2
        if to_status == "claimed":
            sequence -= 1
    return VectorOutboxAuditRecord(
        audit_id="vaud_" + uuid4().hex,
        event_id=event.event_id,
        event_type=event.event_type,
        sequence=sequence,
        from_status=from_status,
        to_status=to_status,
        attempt=event.attempt,
        reason_code=reason_code,
        correlation_id=event.payload.correlation_id,
        occurred_at=occurred_at,
    )
