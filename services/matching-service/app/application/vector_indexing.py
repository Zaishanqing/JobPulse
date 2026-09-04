"""C1/C2 planning and lifecycle services for derived vector indexes."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from pydantic import Field

from app.domain.feature_flags import FeatureFlagController
from app.domain.profiles import ImmutableDTO
from app.domain.vector_contracts import SemanticFragment, deterministic_point_id
from app.domain.vector_indexing import (
    VectorEventType,
    VectorIndexReferenceRecord,
    VectorOutboxEvent,
    VectorOutboxPayload,
    vector_outbox_audit,
    vector_reference_id,
)
from app.ports.repositories import UnitOfWorkFactory

_REVOKE_EVENTS = frozenset({"cv_profile_revoked", "position_profile_revoked"})
_CV_EVENTS = frozenset({"cv_profile_published", "cv_profile_updated", "cv_profile_revoked"})
_POSITION_EVENTS = frozenset(
    {
        "position_profile_published",
        "position_profile_updated",
        "position_profile_revoked",
    }
)


class VectorIndexPlanResult(ImmutableDTO):
    event: VectorOutboxEvent
    references: tuple[VectorIndexReferenceRecord, ...]
    created: bool


class VectorIndexPlanningService:
    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        *,
        clock: Callable[[], datetime] | None = None,
        feature_flags: FeatureFlagController | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._feature_flags = feature_flags

    def plan(
        self,
        *,
        event_type: VectorEventType,
        payload: VectorOutboxPayload,
        fragments: tuple[SemanticFragment, ...],
        embedding_model: str,
        embedding_dimension: int,
        vector_schema_version: str = "vector-record.v1",
        max_attempts: int = 5,
        user_ref: str | None = None,
    ) -> VectorIndexPlanResult:
        if self._feature_flags is not None and not self._feature_flags.enabled(
            "indexing", tenant_ref=payload.tenant_ref, user_ref=user_ref
        ):
            raise ValueError("VECTOR_INDEXING_FEATURE_DISABLED")
        self._validate_request(event_type, payload, fragments, embedding_dimension)
        now = self._clock()
        event = VectorOutboxEvent.create(
            event_type=event_type,
            payload=payload,
            max_attempts=max_attempts,
            now=now,
        )
        with self._unit_of_work() as uow:
            uow.vector_references.lock_entity(
                payload.tenant_ref, payload.entity_type, payload.entity_id
            )
            uow.vector_outbox.lock_deduplication_key(event.deduplication_key)
            duplicate = uow.vector_outbox.get_by_deduplication_key(event.deduplication_key)
            if duplicate is not None:
                references = (
                    uow.vector_references.list_for_entity(
                        payload.tenant_ref,
                        payload.entity_type,
                        payload.entity_id,
                    )
                    if duplicate.event_type in _REVOKE_EVENTS
                    else self._event_references(uow, duplicate.payload)
                )
                uow.commit()
                return VectorIndexPlanResult(event=duplicate, references=references, created=False)

            existing = uow.vector_references.list_for_entity(
                payload.tenant_ref, payload.entity_type, payload.entity_id
            )
            if event_type in _REVOKE_EVENTS:
                references = tuple(
                    self._terminal_reference(item, status="deleted", now=now)
                    if item.status != "deleted"
                    else item
                    for item in existing
                )
                for reference in references:
                    uow.vector_references.save(reference)
            else:
                if event_type == "vector_reindex_requested" and any(
                    item.status not in {"deleted", "superseded"}
                    and item.profile_version != payload.profile_version
                    for item in existing
                ):
                    raise ValueError("cannot reindex a stale profile lineage")
                candidates = tuple(
                    self._reference(
                        fragment,
                        payload=payload,
                        embedding_model=embedding_model,
                        embedding_dimension=embedding_dimension,
                        vector_schema_version=vector_schema_version,
                        now=now,
                    )
                    for fragment in fragments
                )
                stored_references: list[VectorIndexReferenceRecord] = []
                for candidate in candidates:
                    current = uow.vector_references.get(candidate.reference_id)
                    if current is None:
                        uow.vector_references.save(candidate)
                        stored_references.append(candidate)
                    elif event_type == "vector_reindex_requested":
                        reset = current.model_copy(
                            update={
                                "status": "pending",
                                "error_code": None,
                                "indexed_at": None,
                                "updated_at": now,
                            }
                        )
                        uow.vector_references.save(reset)
                        stored_references.append(reset)
                    else:
                        stored_references.append(current)
                references = tuple(stored_references)
            uow.vector_outbox.save(event)
            uow.vector_outbox_audits.append(
                vector_outbox_audit(
                    event,
                    from_status=None,
                    to_status="pending",
                    occurred_at=now,
                )
            )
            uow.commit()
        return VectorIndexPlanResult(event=event, references=references, created=True)

    @staticmethod
    def _validate_request(
        event_type: VectorEventType,
        payload: VectorOutboxPayload,
        fragments: tuple[SemanticFragment, ...],
        embedding_dimension: int,
    ) -> None:
        if not isinstance(embedding_dimension, int) or embedding_dimension <= 0:
            raise ValueError("embedding dimension must be positive")
        if event_type in _CV_EVENTS and payload.entity_type != "cv":
            raise ValueError("CV vector event requires a CV entity")
        if event_type in _POSITION_EVENTS and payload.entity_type != "position":
            raise ValueError("position vector event requires a position entity")
        if event_type not in _REVOKE_EVENTS and not fragments:
            raise ValueError("indexing vector event requires semantic fragments")
        for fragment in fragments:
            if (
                fragment.tenant_ref != payload.tenant_ref
                or fragment.source_type != payload.entity_type
                or fragment.source_id != (payload.source_entity_id or payload.entity_id)
                or fragment.source_profile_id != payload.profile_version
                or fragment.source_version != payload.source_version
                or fragment.grant_id != payload.grant_id
                or fragment.grant_version != payload.grant_version
                or fragment.personal_tenant_ref != payload.personal_tenant_ref
                or fragment.enterprise_tenant_ref != payload.enterprise_tenant_ref
            ):
                raise ValueError("semantic fragment does not match vector event lineage")

    @staticmethod
    def _reference(
        fragment: SemanticFragment,
        *,
        payload: VectorOutboxPayload,
        embedding_model: str,
        embedding_dimension: int,
        vector_schema_version: str,
        now: datetime,
    ) -> VectorIndexReferenceRecord:
        revision = payload.requested_embedding_revision
        return VectorIndexReferenceRecord(
            reference_id=vector_reference_id(
                tenant_ref=payload.tenant_ref,
                entity_type=payload.entity_type,
                entity_id=payload.entity_id,
                fragment_id=fragment.fragment_id,
                profile_version=payload.profile_version,
                embedding_revision=revision,
                grant_id=payload.grant_id,
                grant_version=payload.grant_version,
            ),
            tenant_ref=payload.tenant_ref,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            fragment_id=fragment.fragment_id,
            fragment_type=fragment.fragment_type,
            point_id=deterministic_point_id(
                fragment,
                embedding_model=embedding_model,
                embedding_revision=revision,
                dimension=embedding_dimension,
            ),
            profile_version=payload.profile_version,
            source_version=payload.source_version,
            source_entity_id=payload.source_entity_id,
            target_type=payload.target_type,
            grant_id=payload.grant_id,
            grant_version=payload.grant_version,
            personal_tenant_ref=payload.personal_tenant_ref,
            enterprise_tenant_ref=payload.enterprise_tenant_ref,
            embedding_model=embedding_model,
            embedding_revision=revision,
            vector_schema_version=vector_schema_version,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _terminal_reference(
        reference: VectorIndexReferenceRecord,
        *,
        status: str,
        now: datetime,
    ) -> VectorIndexReferenceRecord:
        updates: dict[str, object] = {
            "status": status,
            "error_code": None,
            "updated_at": now,
        }
        if status == "superseded":
            updates["superseded_at"] = now
        return reference.model_copy(update=updates)

    @staticmethod
    def _event_references(uow, payload) -> tuple[VectorIndexReferenceRecord, ...]:
        return tuple(
            item
            for item in uow.vector_references.list_for_entity(
                payload.tenant_ref, payload.entity_type, payload.entity_id
            )
            if item.profile_version == payload.profile_version
            and item.embedding_revision == payload.requested_embedding_revision
        )


class VectorOutboxLifecycleResult(ImmutableDTO):
    outcome: str = Field(pattern=r"^(idle|claimed|processed|retrying|dead_letter|lost_claim)$")
    event: VectorOutboxEvent | None = None


class VectorOutboxLifecycleService:
    """Transactional C2 state changes; actual vector work belongs to C3."""

    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        *,
        lease_seconds: float = 30,
        retry_seconds: float = 5,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if lease_seconds <= 0 or retry_seconds < 0:
            raise ValueError("vector outbox lease and retry settings are invalid")
        self._unit_of_work = unit_of_work
        self._lease_seconds = lease_seconds
        self._retry_seconds = retry_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def claim(self, worker_id: str, event_id: str | None = None) -> VectorOutboxLifecycleResult:
        if not worker_id:
            raise ValueError("worker id is required")
        now = self._clock()
        with self._unit_of_work() as uow:
            claim = uow.vector_outbox.claim(
                worker_id,
                now,
                now + timedelta(seconds=self._lease_seconds),
                event_id,
            )
            event = claim.event if claim is not None else None
            if claim is not None:
                uow.vector_outbox_audits.append(
                    vector_outbox_audit(
                        event,
                        from_status=claim.from_status,
                        to_status="claimed",
                        occurred_at=now,
                        sequence_override=self._next_audit_sequence(
                            uow, event.event_id
                        ),
                    )
                )
            uow.commit()
        return VectorOutboxLifecycleResult(
            outcome="claimed" if event is not None else "idle", event=event
        )

    def acknowledge(
        self,
        event_id: str,
        worker_id: str,
        reference_ids: tuple[str, ...],
    ) -> VectorOutboxLifecycleResult:
        now = self._clock()
        with self._unit_of_work() as uow:
            current = uow.vector_outbox.get(event_id)
            if current is None or current.status != "claimed" or current.claimed_by != worker_id:
                uow.commit()
                return VectorOutboxLifecycleResult(outcome="lost_claim")
            expected = self._expected_reference_ids(uow, current)
            if frozenset(reference_ids) != expected or len(reference_ids) != len(
                set(reference_ids)
            ):
                raise ValueError("vector ACK does not match event references")
            processed = uow.vector_outbox.mark_processed(
                event_id, worker_id, tuple(sorted(reference_ids)), now
            )
            if processed is None:
                uow.commit()
                return VectorOutboxLifecycleResult(outcome="lost_claim")
            for reference_id in reference_ids:
                reference = uow.vector_references.get(reference_id)
                if reference is None:
                    raise ValueError("vector ACK references an unknown index result")
                if current.event_type not in _REVOKE_EVENTS:
                    uow.vector_references.save(
                        reference.model_copy(
                            update={
                                "status": "indexed",
                                "indexed_at": now,
                                "error_code": None,
                                "updated_at": now,
                            }
                        )
                    )
            if current.event_type not in _REVOKE_EVENTS:
                payload = current.payload
                for reference in uow.vector_references.list_for_entity(
                    payload.tenant_ref, payload.entity_type, payload.entity_id
                ):
                    if (
                        reference.created_at < current.created_at
                        and reference.status not in {"deleted", "superseded"}
                        and (
                            reference.profile_version != payload.profile_version
                            or reference.embedding_revision != payload.requested_embedding_revision
                        )
                    ):
                        uow.vector_references.save(
                            reference.model_copy(
                                update={
                                    "status": "superseded",
                                    "superseded_at": now,
                                    "error_code": None,
                                    "updated_at": now,
                                }
                            )
                        )
            uow.vector_outbox_audits.append(
                vector_outbox_audit(
                    processed,
                    from_status="claimed",
                    to_status="processed",
                    occurred_at=now,
                    sequence_override=self._next_audit_sequence(uow, event_id),
                )
            )
            uow.commit()
        return VectorOutboxLifecycleResult(outcome="processed", event=processed)

    def heartbeat(self, event_id: str, worker_id: str) -> bool:
        now = self._clock()
        with self._unit_of_work() as uow:
            renewed = uow.vector_outbox.heartbeat(
                event_id,
                worker_id,
                now,
                now + timedelta(seconds=self._lease_seconds),
            )
            uow.commit()
        return renewed is not None

    def mark_references(
        self, event_id: str, worker_id: str, status: str
    ) -> VectorOutboxLifecycleResult:
        if status not in {"embedding", "upserting"}:
            raise ValueError("unsupported vector reference transition")
        now = self._clock()
        with self._unit_of_work() as uow:
            current = uow.vector_outbox.get(event_id)
            if current is None or current.status != "claimed" or current.claimed_by != worker_id:
                uow.commit()
                return VectorOutboxLifecycleResult(outcome="lost_claim")
            for reference_id in self._expected_reference_ids(uow, current):
                reference = uow.vector_references.get(reference_id)
                if reference is not None:
                    uow.vector_references.save(
                        reference.model_copy(update={"status": status, "updated_at": now})
                    )
            uow.commit()
        return VectorOutboxLifecycleResult(outcome="claimed", event=current)

    def discard_stale(self, event_id: str, worker_id: str) -> VectorOutboxLifecycleResult:
        now = self._clock()
        with self._unit_of_work() as uow:
            current = uow.vector_outbox.get(event_id)
            if current is None or current.status != "claimed" or current.claimed_by != worker_id:
                uow.commit()
                return VectorOutboxLifecycleResult(outcome="lost_claim")
            for reference_id in self._expected_reference_ids(uow, current):
                reference = uow.vector_references.get(reference_id)
                if reference is not None:
                    uow.vector_references.save(
                        reference.model_copy(
                            update={
                                "status": "superseded",
                                "superseded_at": now,
                                "error_code": "VECTOR_EVENT_STALE",
                                "updated_at": now,
                            }
                        )
                    )
            processed = uow.vector_outbox.mark_processed(event_id, worker_id, (), now)
            if processed is None:
                uow.commit()
                return VectorOutboxLifecycleResult(outcome="lost_claim")
            uow.vector_outbox_audits.append(
                vector_outbox_audit(
                    processed,
                    from_status="claimed",
                    to_status="processed",
                    occurred_at=now,
                    reason_code="VECTOR_EVENT_STALE",
                    sequence_override=self._next_audit_sequence(uow, event_id),
                )
            )
            uow.commit()
        return VectorOutboxLifecycleResult(outcome="processed", event=processed)

    def fail(self, event_id: str, worker_id: str, error_code: str) -> VectorOutboxLifecycleResult:
        if not error_code:
            raise ValueError("vector outbox failure requires an error code")
        now = self._clock()
        with self._unit_of_work() as uow:
            current = uow.vector_outbox.get(event_id)
            failed = uow.vector_outbox.mark_failed(
                event_id,
                worker_id,
                now
                + timedelta(
                    seconds=self._retry_seconds * (2 ** max(current.attempt - 1, 0))
                    if current is not None
                    else self._retry_seconds
                ),
                error_code,
                now,
            )
            if failed is None:
                uow.commit()
                return VectorOutboxLifecycleResult(outcome="lost_claim")
            if current is None:
                raise RuntimeError("claimed vector outbox event disappeared")
            reference_status = "failed" if failed.status == "dead_letter" else "retrying"
            for reference_id in self._expected_reference_ids(uow, current):
                reference = uow.vector_references.get(reference_id)
                if reference is not None and reference.status not in {
                    "deleted",
                    "superseded",
                }:
                    uow.vector_references.save(
                        reference.model_copy(
                            update={
                                "status": reference_status,
                                "error_code": error_code,
                                "updated_at": now,
                            }
                        )
                    )
            uow.vector_outbox_audits.append(
                vector_outbox_audit(
                    failed,
                    from_status="claimed",
                    to_status=failed.status,
                    occurred_at=now,
                    reason_code=error_code,
                    sequence_override=self._next_audit_sequence(uow, event_id),
                )
            )
            uow.commit()
        return VectorOutboxLifecycleResult(outcome=failed.status, event=failed)

    @staticmethod
    def _expected_reference_ids(uow, event: VectorOutboxEvent) -> frozenset[str]:
        payload = event.payload
        return frozenset(
            item.reference_id
            for item in uow.vector_references.list_for_entity(
                payload.tenant_ref, payload.entity_type, payload.entity_id
            )
            if (
                item.status == "deleted"
                if event.event_type in _REVOKE_EVENTS
                else item.profile_version == payload.profile_version
                and item.embedding_revision == payload.requested_embedding_revision
                and item.status not in {"deleted", "superseded"}
            )
        )

    @staticmethod
    def _next_audit_sequence(uow, event_id: str) -> int:
        return max(
            (
                item.sequence
                for item in uow.vector_outbox_audits.list_for_event(event_id)
            ),
            default=-1,
        ) + 1
