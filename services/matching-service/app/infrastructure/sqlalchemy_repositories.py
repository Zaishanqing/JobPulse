"""Transactional SQLAlchemy adapters for PostgreSQL and SQLite test mode."""

from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from types import TracebackType

from sqlalchemy import Engine, Select, and_, create_engine, event, func, or_, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from app.domain.evaluation import MatchEvaluation
from app.domain.gaps import GapAnalysis
from app.domain.outbox import OutboxRecord
from app.domain.profiles import CVMatchProfile, PositionMatchProfile
from app.domain.queue import TaskQueueMessage
from app.domain.tasks import (
    AuditRecord,
    EvaluationTask,
    PersistedEvaluation,
    PersistenceVersions,
    validate_audit_record,
)
from app.domain.vector_indexing import (
    VectorIndexReferenceRecord,
    VectorOutboxAuditRecord,
    VectorOutboxClaim,
    VectorOutboxEvent,
    VectorOutboxPayload,
)
from app.infrastructure.sqlalchemy_models import (
    AuditRecordRow,
    EvaluationTaskRow,
    OutboxRecordRow,
    PersistedEvaluationRow,
    VectorIndexReferenceRow,
    VectorOutboxAuditRow,
    VectorOutboxEventRow,
)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _task_from_row(row: EvaluationTaskRow) -> EvaluationTask:
    return EvaluationTask(
        task_id=row.task_id,
        access_scope=row.access_scope,
        idempotency_key=row.idempotency_key,
        versions=PersistenceVersions.model_validate(row.versions_json),
        status=row.status,
        attempt=row.attempt,
        max_attempts=row.max_attempts,
        evaluation_id=row.evaluation_id,
        error_code=row.error_code,
        error_message=row.error_message,
        lease_owner=row.lease_owner,
        lease_expires_at=_as_utc(row.lease_expires_at) if row.lease_expires_at else None,
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
        cv_profile=CVMatchProfile.model_validate(row.cv_profile_json),
        position_profile=PositionMatchProfile.model_validate(row.position_profile_json),
        target_type=PersistenceVersions.model_validate(row.versions_json).target_type,
    )


def _evaluation_from_row(row: PersistedEvaluationRow) -> PersistedEvaluation:
    return PersistedEvaluation(
        evaluation_id=row.evaluation_id,
        task_id=row.task_id,
        access_scope=row.access_scope,
        idempotency_key=row.idempotency_key,
        versions=PersistenceVersions.model_validate(row.versions_json),
        evaluation=MatchEvaluation.model_validate(row.evaluation_json),
        gap_analysis=GapAnalysis.model_validate(row.gap_analysis_json),
        stale=row.stale,
        stale_reason_codes=tuple(row.stale_reason_codes),
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
    )


def _audit_from_row(row: AuditRecordRow) -> AuditRecord:
    return AuditRecord(
        audit_id=row.audit_id,
        task_id=row.task_id,
        access_scope=row.access_scope,
        event_type=row.event_type,
        from_status=row.from_status,
        to_status=row.to_status,
        attempt=row.attempt,
        reason_code=row.reason_code,
        idempotency_key=row.idempotency_key,
        algorithm_version=row.algorithm_version,
        occurred_at=_as_utc(row.occurred_at),
    )


def _outbox_from_row(row: OutboxRecordRow) -> OutboxRecord:
    return OutboxRecord(
        outbox_id=row.outbox_id,
        access_scope=row.access_scope,
        task_id=row.task_id,
        message_id=row.message_id,
        payload=TaskQueueMessage.model_validate(row.payload),
        status=row.status,
        attempt=row.attempt,
        available_at=_as_utc(row.available_at),
        claimed_by=row.claimed_by,
        claim_expires_at=_as_utc(row.claim_expires_at) if row.claim_expires_at else None,
        published_at=_as_utc(row.published_at) if row.published_at else None,
        last_error_code=row.last_error_code,
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
    )


def _vector_reference_from_row(
    row: VectorIndexReferenceRow,
) -> VectorIndexReferenceRecord:
    return VectorIndexReferenceRecord(
        reference_id=row.reference_id,
        tenant_ref=row.tenant_ref,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        fragment_id=row.fragment_id,
        fragment_type=row.fragment_type,
        point_id=row.point_id,
        profile_version=row.profile_version,
        source_version=row.source_version,
        source_entity_id=row.source_entity_id,
        target_type=row.target_type,
        grant_id=row.grant_id,
        grant_version=row.grant_version,
        personal_tenant_ref=row.personal_tenant_ref,
        enterprise_tenant_ref=row.enterprise_tenant_ref,
        embedding_model=row.embedding_model,
        embedding_revision=row.embedding_revision,
        vector_schema_version=row.vector_schema_version,
        status=row.status,
        error_code=row.error_code,
        indexed_at=_as_utc(row.indexed_at) if row.indexed_at else None,
        superseded_at=_as_utc(row.superseded_at) if row.superseded_at else None,
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
    )


def _vector_event_from_row(row: VectorOutboxEventRow) -> VectorOutboxEvent:
    return VectorOutboxEvent(
        event_id=row.event_id,
        event_type=row.event_type,
        deduplication_key=row.deduplication_key,
        payload=VectorOutboxPayload.model_validate(row.payload),
        status=row.status,
        attempt=row.attempt,
        max_attempts=row.max_attempts,
        available_at=_as_utc(row.available_at),
        claimed_by=row.claimed_by,
        claim_expires_at=_as_utc(row.claim_expires_at) if row.claim_expires_at else None,
        processed_at=_as_utc(row.processed_at) if row.processed_at else None,
        acknowledged_reference_ids=tuple(row.acknowledged_reference_ids),
        last_error_code=row.last_error_code,
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
    )


def _vector_audit_from_row(row: VectorOutboxAuditRow) -> VectorOutboxAuditRecord:
    return VectorOutboxAuditRecord(
        audit_id=row.audit_id,
        event_id=row.event_id,
        event_type=row.event_type,
        sequence=row.sequence,
        from_status=row.from_status,
        to_status=row.to_status,
        attempt=row.attempt,
        reason_code=row.reason_code,
        correlation_id=row.correlation_id,
        occurred_at=_as_utc(row.occurred_at),
    )


class SQLAlchemyTaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, task_id: str, access_scope: str) -> EvaluationTask | None:
        statement = select(EvaluationTaskRow).where(
            EvaluationTaskRow.task_id == task_id,
            EvaluationTaskRow.access_scope == access_scope,
        )
        row = self._session.scalar(self._locked(statement))
        return _task_from_row(row) if row is not None else None

    def get_any(self, task_id: str) -> EvaluationTask | None:
        statement = select(EvaluationTaskRow).where(EvaluationTaskRow.task_id == task_id)
        row = self._session.scalar(self._locked(statement))
        return _task_from_row(row) if row is not None else None

    def find_by_idempotency_key(
        self, idempotency_key: str, access_scope: str
    ) -> tuple[EvaluationTask, ...]:
        statement = (
            select(EvaluationTaskRow)
            .where(
                EvaluationTaskRow.idempotency_key == idempotency_key,
                EvaluationTaskRow.access_scope == access_scope,
            )
            .order_by(EvaluationTaskRow.created_at, EvaluationTaskRow.task_id)
        )
        return tuple(
            _task_from_row(item) for item in self._session.scalars(self._locked(statement)).all()
        )

    def save(self, task: EvaluationTask) -> None:
        key = {"access_scope": task.access_scope, "task_id": task.task_id}
        row = self._session.get(EvaluationTaskRow, key)
        values = {
            "idempotency_key": task.idempotency_key,
            "cv_profile_id": task.cv_profile_id,
            "position_profile_id": task.position_profile_id,
            "versions_json": task.versions.model_dump(mode="json"),
            "version_signature": task.versions.signature,
            "status": task.status,
            "attempt": task.attempt,
            "max_attempts": task.max_attempts,
            "evaluation_id": task.evaluation_id,
            "error_code": task.error_code,
            "error_message": task.error_message,
            "lease_owner": task.lease_owner,
            "lease_expires_at": task.lease_expires_at,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "cv_profile_json": task.cv_profile.model_dump(mode="json"),
            "position_profile_json": task.position_profile.model_dump(mode="json"),
        }
        if row is None:
            self._session.add(EvaluationTaskRow(**key, **values))
            # Establish the parent row before audit/evaluation children are staged.
            # This is a flush inside the caller-owned transaction, never a commit.
            self._session.flush()
        else:
            for name, value in values.items():
                setattr(row, name, value)

    def claim(
        self,
        task_id: str,
        access_scope: str,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> EvaluationTask | None:
        claimable = or_(
            EvaluationTaskRow.status.in_(("pending", "failed")),
            and_(
                EvaluationTaskRow.status == "running",
                or_(
                    EvaluationTaskRow.lease_expires_at.is_(None),
                    EvaluationTaskRow.lease_expires_at <= now,
                ),
            ),
        )
        statement = (
            update(EvaluationTaskRow)
            .where(
                EvaluationTaskRow.task_id == task_id,
                EvaluationTaskRow.access_scope == access_scope,
                EvaluationTaskRow.attempt < EvaluationTaskRow.max_attempts,
                claimable,
            )
            .values(
                status="running",
                attempt=EvaluationTaskRow.attempt + 1,
                lease_owner=lease_owner,
                lease_expires_at=lease_expires_at,
                error_code=None,
                error_message=None,
                updated_at=now,
            )
            .returning(EvaluationTaskRow)
        )
        row = self._session.execute(statement).scalar_one_or_none()
        return _task_from_row(row) if row is not None else None

    def count_by_status(self) -> dict[str, int]:
        counts = {status: 0 for status in ("pending", "running", "succeeded", "failed")}
        rows = self._session.execute(
            select(EvaluationTaskRow.status, func.count()).group_by(EvaluationTaskRow.status)
        )
        for status, count in rows:
            counts[status] = int(count)
        return counts

    def _locked(self, statement: Select[tuple[EvaluationTaskRow]]) -> Select:
        if self._session.bind is not None and self._session.bind.dialect.name == "postgresql":
            return statement.with_for_update()
        return statement


class SQLAlchemyEvaluationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, evaluation_id: str, access_scope: str) -> PersistedEvaluation | None:
        row = self._session.get(
            PersistedEvaluationRow,
            {"access_scope": access_scope, "evaluation_id": evaluation_id},
        )
        return _evaluation_from_row(row) if row is not None else None

    def get_any(self, evaluation_id: str) -> PersistedEvaluation | None:
        statement = select(PersistedEvaluationRow).where(
            PersistedEvaluationRow.evaluation_id == evaluation_id
        )
        row = self._session.scalar(statement)
        return _evaluation_from_row(row) if row is not None else None

    def save(self, result: PersistedEvaluation) -> None:
        key = {"access_scope": result.access_scope, "evaluation_id": result.evaluation_id}
        row = self._session.get(PersistedEvaluationRow, key)
        values = {
            "task_id": result.task_id,
            "idempotency_key": result.idempotency_key,
            "versions_json": result.versions.model_dump(mode="json"),
            "version_signature": result.versions.signature,
            "evaluation_json": result.evaluation.model_dump(mode="json"),
            "gap_analysis_json": result.gap_analysis.model_dump(mode="json"),
            "stale": result.stale,
            "stale_reason_codes": list(result.stale_reason_codes),
            "created_at": result.created_at,
            "updated_at": result.updated_at,
        }
        if row is None:
            self._session.add(PersistedEvaluationRow(**key, **values))
        else:
            for name, value in values.items():
                setattr(row, name, value)


class SQLAlchemyAuditRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, record: AuditRecord) -> None:
        validate_audit_record(record)
        self._session.add(
            AuditRecordRow(
                access_scope=record.access_scope,
                audit_id=record.audit_id,
                task_id=record.task_id,
                event_type=record.event_type,
                from_status=record.from_status,
                to_status=record.to_status,
                attempt=record.attempt,
                reason_code=record.reason_code,
                idempotency_key=record.idempotency_key,
                algorithm_version=record.algorithm_version,
                occurred_at=record.occurred_at,
            )
        )

    def list_for_task(self, task_id: str, access_scope: str) -> tuple[AuditRecord, ...]:
        rows = self._session.scalars(
            select(AuditRecordRow)
            .where(
                AuditRecordRow.task_id == task_id,
                AuditRecordRow.access_scope == access_scope,
            )
            .order_by(AuditRecordRow.occurred_at, AuditRecordRow.audit_id)
        ).all()
        return tuple(_audit_from_row(row) for row in rows)


class SQLAlchemyOutboxRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, outbox_id: str) -> OutboxRecord | None:
        row = self._session.get(OutboxRecordRow, outbox_id)
        return _outbox_from_row(row) if row is not None else None

    def get_for_task(self, task_id: str, access_scope: str) -> OutboxRecord | None:
        row = self._session.scalar(
            select(OutboxRecordRow).where(
                OutboxRecordRow.task_id == task_id,
                OutboxRecordRow.access_scope == access_scope,
            )
        )
        return _outbox_from_row(row) if row is not None else None

    def save(self, record: OutboxRecord) -> None:
        row = self._session.get(OutboxRecordRow, record.outbox_id)
        values = {
            "access_scope": record.access_scope,
            "task_id": record.task_id,
            "message_id": record.message_id,
            "payload": record.payload.model_dump(mode="json"),
            "status": record.status,
            "attempt": record.attempt,
            "available_at": record.available_at,
            "claimed_by": record.claimed_by,
            "claim_expires_at": record.claim_expires_at,
            "published_at": record.published_at,
            "last_error_code": record.last_error_code,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }
        if row is None:
            self._session.add(OutboxRecordRow(outbox_id=record.outbox_id, **values))
        else:
            for name, value in values.items():
                setattr(row, name, value)

    def claim(self, claimed_by, now, claim_expires_at, outbox_id=None):
        claimable = and_(
            OutboxRecordRow.status != "published",
            OutboxRecordRow.available_at <= now,
            or_(
                OutboxRecordRow.status == "pending",
                OutboxRecordRow.claim_expires_at.is_(None),
                OutboxRecordRow.claim_expires_at <= now,
            ),
        )
        statement = select(OutboxRecordRow).where(claimable)
        if outbox_id is not None:
            statement = statement.where(OutboxRecordRow.outbox_id == outbox_id)
        statement = statement.order_by(
            OutboxRecordRow.available_at, OutboxRecordRow.created_at
        ).limit(1)
        if self._session.bind is not None and self._session.bind.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        row = self._session.scalar(statement)
        if row is None:
            return None
        row.status = "claimed"
        row.attempt += 1
        row.claimed_by = claimed_by
        row.claim_expires_at = claim_expires_at
        row.updated_at = now
        self._session.flush()
        return _outbox_from_row(row)

    def mark_published(self, outbox_id, claimed_by, now):
        statement = (
            update(OutboxRecordRow)
            .where(
                OutboxRecordRow.outbox_id == outbox_id,
                OutboxRecordRow.status == "claimed",
                OutboxRecordRow.claimed_by == claimed_by,
            )
            .values(
                status="published",
                claimed_by=None,
                claim_expires_at=None,
                published_at=now,
                last_error_code=None,
                updated_at=now,
            )
            .returning(OutboxRecordRow)
        )
        row = self._session.execute(statement).scalar_one_or_none()
        return _outbox_from_row(row) if row is not None else None

    def release_for_retry(self, outbox_id, claimed_by, available_at, error_code, now):
        statement = (
            update(OutboxRecordRow)
            .where(
                OutboxRecordRow.outbox_id == outbox_id,
                OutboxRecordRow.status == "claimed",
                OutboxRecordRow.claimed_by == claimed_by,
            )
            .values(
                status="pending",
                available_at=available_at,
                claimed_by=None,
                claim_expires_at=None,
                last_error_code=error_code,
                updated_at=now,
            )
            .returning(OutboxRecordRow)
        )
        row = self._session.execute(statement).scalar_one_or_none()
        return _outbox_from_row(row) if row is not None else None


class SQLAlchemyVectorIndexReferenceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_entity(self, tenant_ref: str, entity_type: str, entity_id: str) -> None:
        return None

    def get(self, reference_id: str) -> VectorIndexReferenceRecord | None:
        row = self._session.get(VectorIndexReferenceRow, reference_id)
        return _vector_reference_from_row(row) if row is not None else None

    def list_for_entity(
        self, tenant_ref: str, entity_type: str, entity_id: str
    ) -> tuple[VectorIndexReferenceRecord, ...]:
        rows = self._session.scalars(
            select(VectorIndexReferenceRow)
            .where(
                VectorIndexReferenceRow.tenant_ref == tenant_ref,
                VectorIndexReferenceRow.entity_type == entity_type,
                VectorIndexReferenceRow.entity_id == entity_id,
            )
            .order_by(
                VectorIndexReferenceRow.created_at,
                VectorIndexReferenceRow.reference_id,
            )
        ).all()
        return tuple(_vector_reference_from_row(row) for row in rows)

    def save(self, reference: VectorIndexReferenceRecord) -> None:
        row = self._session.get(VectorIndexReferenceRow, reference.reference_id)
        values = reference.model_dump(mode="python", exclude={"reference_id"})
        if row is None:
            self._session.add(
                VectorIndexReferenceRow(reference_id=reference.reference_id, **values)
            )
        else:
            for name, value in values.items():
                setattr(row, name, value)

    def list_all(self, *, tenant_ref=None, embedding_revision=None, statuses=()):
        statement = select(VectorIndexReferenceRow)
        if tenant_ref is not None:
            statement = statement.where(VectorIndexReferenceRow.tenant_ref == tenant_ref)
        if embedding_revision is not None:
            statement = statement.where(
                VectorIndexReferenceRow.embedding_revision == embedding_revision
            )
        if statuses:
            statement = statement.where(VectorIndexReferenceRow.status.in_(statuses))
        rows = self._session.scalars(
            statement.order_by(
                VectorIndexReferenceRow.created_at,
                VectorIndexReferenceRow.reference_id,
            )
        ).all()
        return tuple(_vector_reference_from_row(row) for row in rows)


class SQLAlchemyVectorOutboxRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_deduplication_key(self, key: str) -> None:
        return None

    def get(self, event_id: str) -> VectorOutboxEvent | None:
        row = self._session.get(VectorOutboxEventRow, event_id)
        return _vector_event_from_row(row) if row is not None else None

    def get_by_deduplication_key(self, key: str) -> VectorOutboxEvent | None:
        row = self._session.scalar(
            select(VectorOutboxEventRow).where(VectorOutboxEventRow.deduplication_key == key)
        )
        return _vector_event_from_row(row) if row is not None else None

    def save(self, event: VectorOutboxEvent) -> None:
        row = self._session.get(VectorOutboxEventRow, event.event_id)
        values = {
            "event_type": event.event_type,
            "deduplication_key": event.deduplication_key,
            "payload": event.payload.model_dump(mode="json"),
            "status": event.status,
            "attempt": event.attempt,
            "max_attempts": event.max_attempts,
            "available_at": event.available_at,
            "claimed_by": event.claimed_by,
            "claim_expires_at": event.claim_expires_at,
            "processed_at": event.processed_at,
            "acknowledged_reference_ids": list(event.acknowledged_reference_ids),
            "last_error_code": event.last_error_code,
            "created_at": event.created_at,
            "updated_at": event.updated_at,
        }
        if row is None:
            self._session.add(VectorOutboxEventRow(event_id=event.event_id, **values))
            self._session.flush()
        else:
            for name, value in values.items():
                setattr(row, name, value)

    def claim(self, claimed_by, now, claim_expires_at, event_id=None):
        claimable = and_(
            VectorOutboxEventRow.available_at <= now,
            or_(
                and_(
                    VectorOutboxEventRow.status.in_(("pending", "retrying")),
                    VectorOutboxEventRow.attempt < VectorOutboxEventRow.max_attempts,
                ),
                and_(
                    VectorOutboxEventRow.status == "claimed",
                    VectorOutboxEventRow.claim_expires_at <= now,
                ),
            ),
        )
        statement = select(VectorOutboxEventRow).where(claimable)
        if event_id is not None:
            statement = statement.where(VectorOutboxEventRow.event_id == event_id)
        statement = statement.order_by(
            VectorOutboxEventRow.available_at, VectorOutboxEventRow.created_at
        ).limit(1)
        if self._session.bind is not None and self._session.bind.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        row = self._session.scalar(statement)
        if row is None:
            return None
        from_status = row.status
        row.status = "claimed"
        row.attempt = min(row.attempt + 1, row.max_attempts)
        row.claimed_by = claimed_by
        row.claim_expires_at = claim_expires_at
        row.last_error_code = None
        row.updated_at = now
        self._session.flush()
        return VectorOutboxClaim(event=_vector_event_from_row(row), from_status=from_status)

    def mark_processed(self, event_id, claimed_by, reference_ids, now):
        statement = (
            update(VectorOutboxEventRow)
            .where(
                VectorOutboxEventRow.event_id == event_id,
                VectorOutboxEventRow.status == "claimed",
                VectorOutboxEventRow.claimed_by == claimed_by,
            )
            .values(
                status="processed",
                claimed_by=None,
                claim_expires_at=None,
                processed_at=now,
                acknowledged_reference_ids=list(reference_ids),
                last_error_code=None,
                updated_at=now,
            )
            .returning(VectorOutboxEventRow)
        )
        row = self._session.execute(statement).scalar_one_or_none()
        return _vector_event_from_row(row) if row is not None else None

    def mark_failed(self, event_id, claimed_by, available_at, error_code, now):
        statement = select(VectorOutboxEventRow).where(
            VectorOutboxEventRow.event_id == event_id,
            VectorOutboxEventRow.status == "claimed",
            VectorOutboxEventRow.claimed_by == claimed_by,
        )
        if self._session.bind is not None and self._session.bind.dialect.name == "postgresql":
            statement = statement.with_for_update()
        row = self._session.scalar(statement)
        if row is None:
            return None
        row.status = "dead_letter" if row.attempt >= row.max_attempts else "retrying"
        row.available_at = available_at
        row.claimed_by = None
        row.claim_expires_at = None
        row.last_error_code = error_code
        row.updated_at = now
        self._session.flush()
        return _vector_event_from_row(row)

    def heartbeat(self, event_id, claimed_by, now, claim_expires_at):
        statement = (
            update(VectorOutboxEventRow)
            .where(
                VectorOutboxEventRow.event_id == event_id,
                VectorOutboxEventRow.status == "claimed",
                VectorOutboxEventRow.claimed_by == claimed_by,
            )
            .values(claim_expires_at=claim_expires_at, updated_at=now)
            .returning(VectorOutboxEventRow)
        )
        row = self._session.execute(statement).scalar_one_or_none()
        return _vector_event_from_row(row) if row is not None else None

    def list_all(self, *, statuses=()):
        statement = select(VectorOutboxEventRow)
        if statuses:
            statement = statement.where(VectorOutboxEventRow.status.in_(statuses))
        rows = self._session.scalars(
            statement.order_by(VectorOutboxEventRow.created_at, VectorOutboxEventRow.event_id)
        ).all()
        return tuple(_vector_event_from_row(row) for row in rows)

    def retry_failed(self, event_ids, now):
        if not event_ids:
            return 0
        result = self._session.execute(
            update(VectorOutboxEventRow)
            .where(
                VectorOutboxEventRow.event_id.in_(event_ids),
                VectorOutboxEventRow.status == "dead_letter",
            )
            .values(
                status="retrying",
                attempt=0,
                available_at=now,
                last_error_code=None,
                updated_at=now,
            )
        )
        return int(result.rowcount or 0)


class SQLAlchemyVectorOutboxAuditRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, record: VectorOutboxAuditRecord) -> None:
        self._session.add(VectorOutboxAuditRow(**record.model_dump(mode="python")))

    def list_for_event(self, event_id: str) -> tuple[VectorOutboxAuditRecord, ...]:
        rows = self._session.scalars(
            select(VectorOutboxAuditRow)
            .where(VectorOutboxAuditRow.event_id == event_id)
            .order_by(VectorOutboxAuditRow.sequence)
        ).all()
        return tuple(_vector_audit_from_row(row) for row in rows)


class SQLAlchemyUnitOfWork:
    def __init__(self, persistence: SQLAlchemyPersistence) -> None:
        self._persistence = persistence
        self._session: Session | None = None
        self._committed = False

    def __enter__(self) -> SQLAlchemyUnitOfWork:
        self._persistence.lock.acquire()
        self._session = self._persistence.session_factory()
        self.tasks = SQLAlchemyTaskRepository(self._session)
        self.evaluations = SQLAlchemyEvaluationRepository(self._session)
        self.audits = SQLAlchemyAuditRepository(self._session)
        self.outbox = SQLAlchemyOutboxRepository(self._session)
        self.vector_references = SQLAlchemyVectorIndexReferenceRepository(self._session)
        self.vector_outbox = SQLAlchemyVectorOutboxRepository(self._session)
        self.vector_outbox_audits = SQLAlchemyVectorOutboxAuditRepository(self._session)
        return self

    def commit(self) -> None:
        self._committed = True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        assert self._session is not None
        try:
            if exc_type is None and self._committed:
                self._session.commit()
            else:
                self._session.rollback()
        except Exception:
            self._session.rollback()
            raise
        finally:
            self._session.close()
            self._persistence.lock.release()
        return False


class SQLAlchemyPersistence:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        self.lock = RLock()

    @classmethod
    def from_url(cls, database_url: str, **engine_options: object) -> SQLAlchemyPersistence:
        engine = create_engine(database_url, **engine_options)
        if engine.dialect.name == "sqlite":

            @event.listens_for(engine, "connect")
            def enable_sqlite_foreign_keys(dbapi_connection: object, _: object) -> None:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

        return cls(engine)

    def unit_of_work(self) -> SQLAlchemyUnitOfWork:
        return SQLAlchemyUnitOfWork(self)

    def check_health(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            # Connectivity alone can report ready while the deployed schema is
            # stale.  This zero-row probe verifies the columns required by task
            # submission without reading candidate data.
            connection.execute(
                text(
                    "SELECT cv_profile_id, position_profile_id "
                    "FROM evaluation_tasks WHERE 1 = 0"
                )
            )
            connection.execute(
                text("SELECT idempotency_key FROM audit_records WHERE 1 = 0")
            )
            connection.execute(
                text(
                    "SELECT idempotency_key FROM persisted_evaluations WHERE 1 = 0"
                )
            )
            connection.execute(
                text(
                    "SELECT profile_version FROM vector_index_references WHERE 1 = 0"
                )
            )

    def dispose(self) -> None:
        self.engine.dispose()
