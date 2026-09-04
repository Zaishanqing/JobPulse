"""Transactional integration events for automatic matching profile indexing."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.domain.json_types import freeze_json_object
from app.integration_events import (
    IdempotencyKey,
    IntegrationEvent,
    OutboxMessageDraft,
)


PROFILE_INDEX_EVENT_TYPE = "matching.vector-index.profile"
PLATFORM_PUBLIC_TENANT_REF = "jobgraph-platform-public"


def tenant_ref(tenant_id: str) -> str:
    return tenant_id


def personal_tenant_ref(user_id: str) -> str:
    return f"personal:{user_id}"


def profile_index_event(
    *,
    vector_event_type: str,
    entity_type: str,
    entity_id: str,
    tenant: str,
    target_type: str,
    source_entity_id: str | None = None,
    grant_id: str | None = None,
    grant_version: int | None = None,
    personal_tenant: str | None = None,
    enterprise_tenant: str | None = None,
    snapshot_id: str | None = None,
    snapshot_revision: int | None = None,
    source_version: str | None = None,
    enterprise_job_id: str | None = None,
) -> OutboxMessageDraft:
    event_id = str(uuid4())
    now = datetime.now(timezone.utc)
    values: dict[str, object] = {
        "schema_version": "matching-vector-profile-event.v1",
        "event_id": event_id,
        "event_type": PROFILE_INDEX_EVENT_TYPE,
        "vector_event_type": vector_event_type,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "tenant_ref": tenant,
        "target_type": target_type,
    }
    optional = {
        "source_entity_id": source_entity_id,
        "grant_id": grant_id,
        "grant_version": grant_version,
        "personal_tenant_ref": personal_tenant,
        "enterprise_tenant_ref": enterprise_tenant,
        "snapshot_id": snapshot_id,
        "snapshot_revision": snapshot_revision,
        "source_version": source_version,
        "enterprise_job_id": enterprise_job_id,
    }
    values.update({key: value for key, value in optional.items() if value is not None})
    return OutboxMessageDraft(
        IntegrationEvent(
            event_id=event_id,
            event_type=PROFILE_INDEX_EVENT_TYPE,
            aggregate_id=entity_id,
            payload=freeze_json_object(values),
            occurred_at=now,
            trace_id=event_id,
        ),
        IdempotencyKey(f"matching-vector-profile:{event_id}"),
    )


def enterprise_projection_entity_id(cv_id: str, grant_id: str) -> str:
    return f"{cv_id}@grant:{grant_id}"


__all__ = [
    "PLATFORM_PUBLIC_TENANT_REF",
    "PROFILE_INDEX_EVENT_TYPE",
    "enterprise_projection_entity_id",
    "personal_tenant_ref",
    "profile_index_event",
    "tenant_ref",
]
