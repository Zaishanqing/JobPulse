"""Persistence ports. Repositories stage changes; the application owns commit."""

from __future__ import annotations

from datetime import datetime
from types import TracebackType
from typing import Protocol

from app.domain.outbox import OutboxRecord
from app.domain.tasks import AuditRecord, EvaluationTask, PersistedEvaluation
from app.domain.vector_indexing import (
    VectorIndexReferenceRecord,
    VectorOutboxAuditRecord,
    VectorOutboxClaim,
    VectorOutboxEvent,
)


class TaskRepository(Protocol):
    def get(self, task_id: str, access_scope: str) -> EvaluationTask | None: ...

    def get_any(self, task_id: str) -> EvaluationTask | None: ...

    def find_by_idempotency_key(
        self, idempotency_key: str, access_scope: str
    ) -> tuple[EvaluationTask, ...]: ...

    def save(self, task: EvaluationTask) -> None: ...

    def claim(
        self,
        task_id: str,
        access_scope: str,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> EvaluationTask | None: ...

    def count_by_status(self) -> dict[str, int]: ...


class EvaluationRepository(Protocol):
    def get(self, evaluation_id: str, access_scope: str) -> PersistedEvaluation | None: ...

    def get_any(self, evaluation_id: str) -> PersistedEvaluation | None: ...

    def save(self, result: PersistedEvaluation) -> None: ...


class AuditRepository(Protocol):
    def append(self, record: AuditRecord) -> None: ...

    def list_for_task(self, task_id: str, access_scope: str) -> tuple[AuditRecord, ...]: ...


class OutboxRepository(Protocol):
    def get(self, outbox_id: str) -> OutboxRecord | None: ...

    def get_for_task(self, task_id: str, access_scope: str) -> OutboxRecord | None: ...

    def save(self, record: OutboxRecord) -> None: ...

    def claim(
        self,
        claimed_by: str,
        now: datetime,
        claim_expires_at: datetime,
        outbox_id: str | None = None,
    ) -> OutboxRecord | None: ...

    def mark_published(
        self, outbox_id: str, claimed_by: str, now: datetime
    ) -> OutboxRecord | None: ...

    def release_for_retry(
        self,
        outbox_id: str,
        claimed_by: str,
        available_at: datetime,
        error_code: str,
        now: datetime,
    ) -> OutboxRecord | None: ...


class VectorIndexReferenceRepository(Protocol):
    def lock_entity(self, tenant_ref: str, entity_type: str, entity_id: str) -> None: ...

    def get(self, reference_id: str) -> VectorIndexReferenceRecord | None: ...

    def list_for_entity(
        self, tenant_ref: str, entity_type: str, entity_id: str
    ) -> tuple[VectorIndexReferenceRecord, ...]: ...

    def save(self, reference: VectorIndexReferenceRecord) -> None: ...

    def list_all(
        self,
        *,
        tenant_ref: str | None = None,
        embedding_revision: str | None = None,
        statuses: tuple[str, ...] = (),
    ) -> tuple[VectorIndexReferenceRecord, ...]: ...


class VectorOutboxRepository(Protocol):
    def lock_deduplication_key(self, key: str) -> None: ...

    def get(self, event_id: str) -> VectorOutboxEvent | None: ...

    def get_by_deduplication_key(self, key: str) -> VectorOutboxEvent | None: ...

    def save(self, event: VectorOutboxEvent) -> None: ...

    def claim(
        self,
        claimed_by: str,
        now: datetime,
        claim_expires_at: datetime,
        event_id: str | None = None,
    ) -> VectorOutboxClaim | None: ...

    def mark_processed(
        self,
        event_id: str,
        claimed_by: str,
        reference_ids: tuple[str, ...],
        now: datetime,
    ) -> VectorOutboxEvent | None: ...

    def mark_failed(
        self,
        event_id: str,
        claimed_by: str,
        available_at: datetime,
        error_code: str,
        now: datetime,
    ) -> VectorOutboxEvent | None: ...

    def heartbeat(
        self,
        event_id: str,
        claimed_by: str,
        now: datetime,
        claim_expires_at: datetime,
    ) -> VectorOutboxEvent | None: ...

    def list_all(self, *, statuses: tuple[str, ...] = ()) -> tuple[VectorOutboxEvent, ...]: ...

    def retry_failed(self, event_ids: tuple[str, ...], now: datetime) -> int: ...


class VectorOutboxAuditRepository(Protocol):
    def append(self, record: VectorOutboxAuditRecord) -> None: ...

    def list_for_event(self, event_id: str) -> tuple[VectorOutboxAuditRecord, ...]: ...


class RepositoryUnitOfWork(Protocol):
    tasks: TaskRepository
    evaluations: EvaluationRepository
    audits: AuditRepository
    outbox: OutboxRepository
    vector_references: VectorIndexReferenceRepository
    vector_outbox: VectorOutboxRepository
    vector_outbox_audits: VectorOutboxAuditRepository

    def __enter__(self) -> RepositoryUnitOfWork: ...

    def commit(self) -> None: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> RepositoryUnitOfWork: ...
