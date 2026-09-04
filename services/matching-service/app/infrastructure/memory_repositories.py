"""Thread-safe in-memory repositories and transactional unit of work."""

from __future__ import annotations

from threading import RLock
from types import TracebackType

from app.domain.outbox import OutboxRecord
from app.domain.tasks import (
    AuditRecord,
    EvaluationTask,
    PersistedEvaluation,
    validate_audit_record,
)
from app.domain.vector_indexing import (
    VectorIndexReferenceRecord,
    VectorOutboxAuditRecord,
    VectorOutboxClaim,
    VectorOutboxEvent,
)


class InMemoryPersistence:
    """Shared storage for development and tests; not a production database adapter."""

    def __init__(self) -> None:
        self.tasks: dict[tuple[str, str], EvaluationTask] = {}
        self.evaluations: dict[tuple[str, str], PersistedEvaluation] = {}
        self.audit_records: list[AuditRecord] = []
        self.outbox_records: dict[str, OutboxRecord] = {}
        self.vector_references: dict[str, VectorIndexReferenceRecord] = {}
        self.vector_outbox_events: dict[str, VectorOutboxEvent] = {}
        self.vector_outbox_audit_records: list[VectorOutboxAuditRecord] = []
        self.lock = RLock()

    def unit_of_work(self) -> InMemoryUnitOfWork:
        return InMemoryUnitOfWork(self)


class InMemoryTaskRepository:
    def __init__(self, storage: InMemoryPersistence) -> None:
        self._storage = storage

    def get(self, task_id: str, access_scope: str) -> EvaluationTask | None:
        return self._storage.tasks.get((access_scope, task_id))

    def get_any(self, task_id: str) -> EvaluationTask | None:
        for (__, stored_task_id), task in self._storage.tasks.items():
            if stored_task_id == task_id:
                return task
        return None

    def find_by_idempotency_key(
        self, idempotency_key: str, access_scope: str
    ) -> tuple[EvaluationTask, ...]:
        items = (
            task
            for (scope, _), task in self._storage.tasks.items()
            if scope == access_scope and task.idempotency_key == idempotency_key
        )
        return tuple(sorted(items, key=lambda item: (item.created_at, item.task_id)))

    def save(self, task: EvaluationTask) -> None:
        self._storage.tasks[(task.access_scope, task.task_id)] = task

    def claim(
        self, task_id, access_scope, lease_owner, now, lease_expires_at
    ) -> EvaluationTask | None:
        task = self.get(task_id, access_scope)
        if task is None or task.status == "succeeded" or task.attempt >= task.max_attempts:
            return None
        claimable = task.status in {"pending", "failed"} or (
            task.status == "running"
            and (task.lease_expires_at is None or task.lease_expires_at <= now)
        )
        if not claimable:
            return None
        claimed = task.model_copy(
            update={
                "status": "running",
                "attempt": task.attempt + 1,
                "lease_owner": lease_owner,
                "lease_expires_at": lease_expires_at,
                "error_code": None,
                "error_message": None,
                "updated_at": now,
            }
        )
        self.save(claimed)
        return claimed

    def count_by_status(self) -> dict[str, int]:
        counts = {status: 0 for status in ("pending", "running", "succeeded", "failed")}
        for task in self._storage.tasks.values():
            counts[task.status] += 1
        return counts


class InMemoryEvaluationRepository:
    def __init__(self, storage: InMemoryPersistence) -> None:
        self._storage = storage

    def get(self, evaluation_id: str, access_scope: str) -> PersistedEvaluation | None:
        return self._storage.evaluations.get((access_scope, evaluation_id))

    def get_any(self, evaluation_id: str) -> PersistedEvaluation | None:
        for (__, stored_evaluation_id), result in self._storage.evaluations.items():
            if stored_evaluation_id == evaluation_id:
                return result
        return None

    def save(self, result: PersistedEvaluation) -> None:
        self._storage.evaluations[(result.access_scope, result.evaluation_id)] = result


class InMemoryAuditRepository:
    def __init__(self, storage: InMemoryPersistence) -> None:
        self._storage = storage

    def append(self, record: AuditRecord) -> None:
        validate_audit_record(record)
        self._storage.audit_records.append(record)

    def list_for_task(self, task_id: str, access_scope: str) -> tuple[AuditRecord, ...]:
        return tuple(
            item
            for item in self._storage.audit_records
            if item.task_id == task_id and item.access_scope == access_scope
        )


class InMemoryOutboxRepository:
    def __init__(self, storage: InMemoryPersistence) -> None:
        self._storage = storage

    def get(self, outbox_id: str) -> OutboxRecord | None:
        return self._storage.outbox_records.get(outbox_id)

    def get_for_task(self, task_id: str, access_scope: str) -> OutboxRecord | None:
        return next(
            (
                item
                for item in self._storage.outbox_records.values()
                if item.task_id == task_id and item.access_scope == access_scope
            ),
            None,
        )

    def save(self, record: OutboxRecord) -> None:
        self._storage.outbox_records[record.outbox_id] = record

    def claim(self, claimed_by, now, claim_expires_at, outbox_id=None):
        candidates = (
            item
            for item in self._storage.outbox_records.values()
            if (outbox_id is None or item.outbox_id == outbox_id)
            and item.status != "published"
            and item.available_at <= now
            and (
                item.status == "pending"
                or item.claim_expires_at is None
                or item.claim_expires_at <= now
            )
        )
        record = min(
            candidates,
            key=lambda item: (item.available_at, item.created_at),
            default=None,
        )
        if record is None:
            return None
        claimed = record.model_copy(
            update={
                "status": "claimed",
                "attempt": record.attempt + 1,
                "claimed_by": claimed_by,
                "claim_expires_at": claim_expires_at,
                "updated_at": now,
            }
        )
        self.save(claimed)
        return claimed

    def mark_published(self, outbox_id, claimed_by, now):
        record = self.get(outbox_id)
        if record is None or record.status != "claimed" or record.claimed_by != claimed_by:
            return None
        published = record.model_copy(
            update={
                "status": "published",
                "claimed_by": None,
                "claim_expires_at": None,
                "published_at": now,
                "last_error_code": None,
                "updated_at": now,
            }
        )
        self.save(published)
        return published

    def release_for_retry(self, outbox_id, claimed_by, available_at, error_code, now):
        record = self.get(outbox_id)
        if record is None or record.status != "claimed" or record.claimed_by != claimed_by:
            return None
        pending = record.model_copy(
            update={
                "status": "pending",
                "available_at": available_at,
                "claimed_by": None,
                "claim_expires_at": None,
                "last_error_code": error_code,
                "updated_at": now,
            }
        )
        self.save(pending)
        return pending


class InMemoryVectorIndexReferenceRepository:
    def __init__(self, storage: InMemoryPersistence) -> None:
        self._storage = storage

    def lock_entity(self, tenant_ref: str, entity_type: str, entity_id: str) -> None:
        del tenant_ref, entity_type, entity_id

    def get(self, reference_id: str) -> VectorIndexReferenceRecord | None:
        return self._storage.vector_references.get(reference_id)

    def list_for_entity(
        self, tenant_ref: str, entity_type: str, entity_id: str
    ) -> tuple[VectorIndexReferenceRecord, ...]:
        items = (
            item
            for item in self._storage.vector_references.values()
            if item.tenant_ref == tenant_ref
            and item.entity_type == entity_type
            and item.entity_id == entity_id
        )
        return tuple(sorted(items, key=lambda item: (item.created_at, item.reference_id)))

    def save(self, reference: VectorIndexReferenceRecord) -> None:
        self._storage.vector_references[reference.reference_id] = reference

    def list_all(self, *, tenant_ref=None, embedding_revision=None, statuses=()):
        items = (
            item
            for item in self._storage.vector_references.values()
            if (tenant_ref is None or item.tenant_ref == tenant_ref)
            and (embedding_revision is None or item.embedding_revision == embedding_revision)
            and (not statuses or item.status in statuses)
        )
        return tuple(sorted(items, key=lambda item: (item.created_at, item.reference_id)))


class InMemoryVectorOutboxRepository:
    def __init__(self, storage: InMemoryPersistence) -> None:
        self._storage = storage

    def lock_deduplication_key(self, key: str) -> None:
        del key

    def get(self, event_id: str) -> VectorOutboxEvent | None:
        return self._storage.vector_outbox_events.get(event_id)

    def get_by_deduplication_key(self, key: str) -> VectorOutboxEvent | None:
        return next(
            (
                item
                for item in self._storage.vector_outbox_events.values()
                if item.deduplication_key == key
            ),
            None,
        )

    def save(self, event: VectorOutboxEvent) -> None:
        self._storage.vector_outbox_events[event.event_id] = event

    def claim(self, claimed_by, now, claim_expires_at, event_id=None):
        candidates = (
            item
            for item in self._storage.vector_outbox_events.values()
            if (event_id is None or item.event_id == event_id)
            and item.available_at <= now
            and (
                (item.status in {"pending", "retrying"} and item.attempt < item.max_attempts)
                or (
                    item.status == "claimed"
                    and item.claim_expires_at is not None
                    and item.claim_expires_at <= now
                )
            )
        )
        event = min(
            candidates,
            key=lambda item: (item.available_at, item.created_at, item.event_id),
            default=None,
        )
        if event is None:
            return None
        from_status = event.status
        claimed = event.model_copy(
            update={
                "status": "claimed",
                "attempt": min(event.attempt + 1, event.max_attempts),
                "claimed_by": claimed_by,
                "claim_expires_at": claim_expires_at,
                "last_error_code": None,
                "updated_at": now,
            }
        )
        self.save(claimed)
        return VectorOutboxClaim(event=claimed, from_status=from_status)

    def mark_processed(self, event_id, claimed_by, reference_ids, now):
        event = self.get(event_id)
        if event is None or event.status != "claimed" or event.claimed_by != claimed_by:
            return None
        processed = event.model_copy(
            update={
                "status": "processed",
                "claimed_by": None,
                "claim_expires_at": None,
                "processed_at": now,
                "acknowledged_reference_ids": reference_ids,
                "last_error_code": None,
                "updated_at": now,
            }
        )
        self.save(processed)
        return processed

    def mark_failed(self, event_id, claimed_by, available_at, error_code, now):
        event = self.get(event_id)
        if event is None or event.status != "claimed" or event.claimed_by != claimed_by:
            return None
        failed = event.model_copy(
            update={
                "status": "dead_letter" if event.attempt >= event.max_attempts else "retrying",
                "available_at": available_at,
                "claimed_by": None,
                "claim_expires_at": None,
                "last_error_code": error_code,
                "updated_at": now,
            }
        )
        self.save(failed)
        return failed

    def heartbeat(self, event_id, claimed_by, now, claim_expires_at):
        event = self.get(event_id)
        if event is None or event.status != "claimed" or event.claimed_by != claimed_by:
            return None
        renewed = event.model_copy(update={"claim_expires_at": claim_expires_at, "updated_at": now})
        self.save(renewed)
        return renewed

    def list_all(self, *, statuses=()):
        items = (
            item
            for item in self._storage.vector_outbox_events.values()
            if not statuses or item.status in statuses
        )
        return tuple(sorted(items, key=lambda item: (item.created_at, item.event_id)))

    def retry_failed(self, event_ids, now):
        count = 0
        for event_id in event_ids:
            event = self.get(event_id)
            if event is None or event.status != "dead_letter":
                continue
            self.save(
                event.model_copy(
                    update={
                        "status": "retrying",
                        "attempt": 0,
                        "available_at": now,
                        "last_error_code": None,
                        "updated_at": now,
                    }
                )
            )
            count += 1
        return count


class InMemoryVectorOutboxAuditRepository:
    def __init__(self, storage: InMemoryPersistence) -> None:
        self._storage = storage

    def append(self, record: VectorOutboxAuditRecord) -> None:
        self._storage.vector_outbox_audit_records.append(record)

    def list_for_event(self, event_id: str) -> tuple[VectorOutboxAuditRecord, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self._storage.vector_outbox_audit_records
                    if item.event_id == event_id
                ),
                key=lambda item: item.sequence,
            )
        )


class InMemoryUnitOfWork:
    """Copy-on-transaction rollback keeps multi-repository writes atomic."""

    def __init__(self, storage: InMemoryPersistence) -> None:
        self._storage = storage
        self.tasks = InMemoryTaskRepository(storage)
        self.evaluations = InMemoryEvaluationRepository(storage)
        self.audits = InMemoryAuditRepository(storage)
        self.outbox = InMemoryOutboxRepository(storage)
        self.vector_references = InMemoryVectorIndexReferenceRepository(storage)
        self.vector_outbox = InMemoryVectorOutboxRepository(storage)
        self.vector_outbox_audits = InMemoryVectorOutboxAuditRepository(storage)
        self._committed = False
        self._task_snapshot: dict[tuple[str, str], EvaluationTask] = {}
        self._evaluation_snapshot: dict[tuple[str, str], PersistedEvaluation] = {}
        self._audit_snapshot: list[AuditRecord] = []
        self._outbox_snapshot: dict[str, OutboxRecord] = {}
        self._vector_reference_snapshot: dict[str, VectorIndexReferenceRecord] = {}
        self._vector_outbox_snapshot: dict[str, VectorOutboxEvent] = {}
        self._vector_audit_snapshot: list[VectorOutboxAuditRecord] = []

    def __enter__(self) -> InMemoryUnitOfWork:
        self._storage.lock.acquire()
        self._task_snapshot = dict(self._storage.tasks)
        self._evaluation_snapshot = dict(self._storage.evaluations)
        self._audit_snapshot = list(self._storage.audit_records)
        self._outbox_snapshot = dict(self._storage.outbox_records)
        self._vector_reference_snapshot = dict(self._storage.vector_references)
        self._vector_outbox_snapshot = dict(self._storage.vector_outbox_events)
        self._vector_audit_snapshot = list(self._storage.vector_outbox_audit_records)
        return self

    def commit(self) -> None:
        self._committed = True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        if exc_type is not None or not self._committed:
            self._storage.tasks = self._task_snapshot
            self._storage.evaluations = self._evaluation_snapshot
            self._storage.audit_records = self._audit_snapshot
            self._storage.outbox_records = self._outbox_snapshot
            self._storage.vector_references = self._vector_reference_snapshot
            self._storage.vector_outbox_events = self._vector_outbox_snapshot
            self._storage.vector_outbox_audit_records = self._vector_audit_snapshot
        self._storage.lock.release()
        return False
