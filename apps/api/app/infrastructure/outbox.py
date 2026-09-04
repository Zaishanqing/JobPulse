from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import datetime, timedelta, timezone
import logging
from threading import Event, Thread
from uuid import uuid4

from sqlalchemy import or_, update
from sqlalchemy.orm import Session, sessionmaker

from app.domain.json_types import freeze_json_object, thaw_json_object
from app.integration_events import (
    DispatchResult,
    IdempotencyKey,
    IntegrationEvent,
    IntegrationEventHandler,
    OutboxMessageDraft,
    OutboxMessageRecord,
    OutboxStatus,
)
from app.models.outbox_message import OutboxMessage
from app.contexts.platform import (
    OutboxEventNotFound,
    OutboxRequeueConflict,
)


LOGGER = logging.getLogger(__name__)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _record(row: OutboxMessage) -> OutboxMessageRecord:
    event = IntegrationEvent(
        event_id=row.event_id,
        event_type=row.event_type,
        aggregate_id=row.aggregate_id,
        payload=freeze_json_object(row.payload),
        occurred_at=_utc(row.occurred_at),
        trace_id=row.trace_id,
    )
    return OutboxMessageRecord(
        message_id=row.id,
        draft=OutboxMessageDraft(event, IdempotencyKey(row.idempotency_key)),
        status=OutboxStatus(row.status),
        attempts=row.attempts,
        next_attempt_at=_utc(row.next_attempt_at),
        lease_owner=row.lease_owner,
        lease_until=_utc(row.lease_until) if row.lease_until else None,
        last_error=row.last_error,
    )


class SqlAlchemyOutboxRepository:
    def __init__(self, session: Session, *, lease_seconds: int = 60, max_attempts: int = 5) -> None:
        self.session = session
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts

    def add(self, draft: OutboxMessageDraft) -> OutboxMessageRecord:
        existing = (
            self.session.query(OutboxMessage)
            .filter_by(idempotency_key=draft.idempotency_key.value)
            .one_or_none()
        )
        if existing is not None:
            return _record(existing)
        now = draft.event.occurred_at
        row = OutboxMessage(
            id=str(uuid4()),
            event_id=draft.event.event_id,
            event_type=draft.event.event_type,
            aggregate_id=draft.event.aggregate_id,
            idempotency_key=draft.idempotency_key.value,
            payload=thaw_json_object(draft.event.payload),
            status=OutboxStatus.PENDING.value,
            attempts=0,
            next_attempt_at=now,
            trace_id=draft.event.trace_id,
            occurred_at=now,
            created_at=now,
            updated_at=now,
        )
        self.session.add(row)
        self.session.flush()
        return _record(row)

    def requeue(self, event_id: str, now: datetime) -> OutboxMessageRecord:
        row = (
            self.session.query(OutboxMessage)
            .filter(OutboxMessage.event_id == event_id)
            .with_for_update()
            .one_or_none()
        )
        if row is None:
            raise OutboxEventNotFound("Outbox event not found")
        status = OutboxStatus(row.status)
        lease_active = (
            status == OutboxStatus.CLAIMED
            and row.lease_until is not None
            and _utc(row.lease_until) > _utc(now)
        )
        if status == OutboxStatus.DELIVERED:
            raise OutboxRequeueConflict("Delivered outbox event cannot be requeued")
        if lease_active:
            raise OutboxRequeueConflict("Active claimed outbox event cannot be requeued")
        if status == OutboxStatus.PENDING:
            if row.attempts > 0 or row.last_error is not None:
                return _record(row)
            raise OutboxRequeueConflict("Pending outbox event has not failed")
        if status not in {
            OutboxStatus.RETRYABLE,
            OutboxStatus.DEAD_LETTER,
            OutboxStatus.CLAIMED,
        }:
            raise OutboxRequeueConflict("Outbox event cannot be requeued")
        row.status = OutboxStatus.PENDING.value
        row.next_attempt_at = now
        row.lease_owner = None
        row.lease_until = None
        row.updated_at = now
        self.session.flush()
        return _record(row)

    def claim(
        self,
        worker_id: str,
        now: datetime,
        *,
        event_types: Collection[str] | None = None,
    ) -> OutboxMessageRecord | None:
        return self._claim(worker_id, now, event_types=event_types)

    def claim_by_id(
        self, message_id: str, worker_id: str, now: datetime
    ) -> OutboxMessageRecord | None:
        return self._claim(worker_id, now, message_id=message_id)

    def renew_lease(self, message_id: str, worker_id: str, now: datetime) -> bool:
        changed = self.session.execute(
            update(OutboxMessage)
            .where(
                OutboxMessage.id == message_id,
                OutboxMessage.status == OutboxStatus.CLAIMED.value,
                OutboxMessage.lease_owner == worker_id,
                OutboxMessage.lease_until > now,
            )
            .values(
                lease_until=now + timedelta(seconds=self.lease_seconds),
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if changed.rowcount != 1:
            return False
        self.session.flush()
        return True

    def _claim(
        self,
        worker_id: str,
        now: datetime,
        *,
        message_id: str | None = None,
        event_types: Collection[str] | None = None,
    ) -> OutboxMessageRecord | None:
        query = self.session.query(OutboxMessage).filter(
            OutboxMessage.status.in_(
                (
                    OutboxStatus.PENDING.value,
                    OutboxStatus.RETRYABLE.value,
                    OutboxStatus.CLAIMED.value,
                )
            ),
            OutboxMessage.next_attempt_at <= now,
            or_(OutboxMessage.lease_until.is_(None), OutboxMessage.lease_until <= now),
        )
        if message_id is not None:
            query = query.filter(OutboxMessage.id == message_id)
        if event_types is not None:
            if not event_types:
                return None
            query = query.filter(OutboxMessage.event_type.in_(tuple(event_types)))
        eligible = query.order_by(OutboxMessage.created_at, OutboxMessage.id).first()
        if eligible is None:
            return None
        changed = self.session.execute(
            update(OutboxMessage)
            .where(
                OutboxMessage.id == eligible.id,
                OutboxMessage.status.in_(
                    (
                        OutboxStatus.PENDING.value,
                        OutboxStatus.RETRYABLE.value,
                        OutboxStatus.CLAIMED.value,
                    )
                ),
                or_(OutboxMessage.lease_until.is_(None), OutboxMessage.lease_until <= now),
            )
            .values(
                status=OutboxStatus.CLAIMED.value,
                lease_owner=worker_id,
                lease_until=now + timedelta(seconds=self.lease_seconds),
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if changed.rowcount != 1:
            return None
        self.session.flush()
        self.session.expire_all()
        return _record(self.session.get(OutboxMessage, eligible.id))

    def complete(self, message_id: str, worker_id: str, result: DispatchResult) -> bool:
        now = datetime.now(timezone.utc)
        row = (
            self.session.query(OutboxMessage)
            .filter(
                OutboxMessage.id == message_id,
                OutboxMessage.status == OutboxStatus.CLAIMED.value,
                OutboxMessage.lease_owner == worker_id,
                OutboxMessage.lease_until > now,
            )
            .with_for_update()
            .one_or_none()
        )
        if row is None:
            return False
        row.attempts += 1
        row.lease_owner = None
        row.lease_until = None
        row.last_error = result.error
        row.updated_at = now
        if result.delivered:
            row.status = OutboxStatus.DELIVERED.value
        elif result.retryable and row.attempts < self.max_attempts:
            row.status = OutboxStatus.RETRYABLE.value
            row.next_attempt_at = now + timedelta(seconds=min(300, 2**row.attempts))
        else:
            row.status = OutboxStatus.DEAD_LETTER.value
        self.session.flush()
        return True


class OutboxLeaseHeartbeat:
    """Renew one claimed message while its handler performs remote work."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        message_id: str,
        worker_id: str,
        lease_seconds: int,
    ) -> None:
        self._session_factory = session_factory
        self._message_id = message_id
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._interval_seconds = max(lease_seconds / 3, 0.1)
        self._stop = Event()
        self._lost_lease = Event()
        self._thread: Thread | None = None

    @property
    def lost_lease(self) -> bool:
        return self._lost_lease.is_set()

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("Outbox lease heartbeat already started")
        self._thread = Thread(
            target=self._run,
            name=f"outbox-lease:{self._message_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            now = datetime.now(timezone.utc)
            try:
                with self._session_factory() as session:
                    repository = SqlAlchemyOutboxRepository(
                        session, lease_seconds=self._lease_seconds
                    )
                    renewed = repository.renew_lease(
                        self._message_id, self._worker_id, now
                    )
                    session.commit()
            except Exception:
                LOGGER.exception(
                    "outbox_lease_renew_failed",
                    extra={
                        "message_id": self._message_id,
                        "worker_id": self._worker_id,
                    },
                )
                self._lost_lease.set()
                return
            if not renewed:
                self._lost_lease.set()
                LOGGER.warning(
                    "outbox_lease_lost",
                    extra={
                        "message_id": self._message_id,
                        "worker_id": self._worker_id,
                    },
                )
                return


class SqlAlchemyOutboxDispatcher:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        handlers: Mapping[str, IntegrationEventHandler],
        *,
        event_types: Collection[str] | None = None,
        lease_seconds: int = 60,
        max_attempts: int = 5,
    ) -> None:
        self.session_factory = session_factory
        self.handlers = handlers
        self.event_types = frozenset(event_types) if event_types is not None else None
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts

    def dispatch_one(self, worker_id: str, now: datetime) -> DispatchResult | None:
        with self.session_factory() as session:
            repository = SqlAlchemyOutboxRepository(
                session,
                lease_seconds=self.lease_seconds,
                max_attempts=self.max_attempts,
            )
            message = repository.claim(
                worker_id, now, event_types=self.event_types
            )
            if message is None:
                session.commit()
                return None
            session.commit()
        handler = self.handlers.get(message.draft.event.event_type)
        if handler is None:
            # A configuration error must release the lease through ``complete``
            # and become actionable in the dead-letter queue.  Retrying cannot
            # make an absent handler appear, so it is explicitly permanent.
            result = DispatchResult(
                False,
                False,
                f"No outbox handler registered for event type: {message.draft.event.event_type}",
            )
        else:
            heartbeat = OutboxLeaseHeartbeat(
                self.session_factory,
                message_id=message.message_id,
                worker_id=worker_id,
                lease_seconds=self.lease_seconds,
            )
            heartbeat.start()
            try:
                try:
                    result = handler.handle(message.draft.event, message.draft.idempotency_key)
                except Exception as exc:
                    result = DispatchResult(False, True, f"handler_exception:{type(exc).__name__}")
            finally:
                heartbeat.stop()
        with self.session_factory() as session:
            completed = SqlAlchemyOutboxRepository(
                session,
                lease_seconds=self.lease_seconds,
                max_attempts=self.max_attempts,
            ).complete(message.message_id, worker_id, result)
            session.commit()
        if not completed:
            return DispatchResult(False, True, "LOST_LEASE")
        return result


class SqlAlchemyOutboxOperationsUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self.session: Session | None = None
        self.outbox: SqlAlchemyOutboxRepository

    def __enter__(self) -> "SqlAlchemyOutboxOperationsUnitOfWork":
        self.session = self._session_factory()
        self.outbox = SqlAlchemyOutboxRepository(self.session)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        assert self.session is not None
        if exc_type is not None:
            self.session.rollback()
        self.session.close()
        self.session = None

    def commit(self) -> None:
        assert self.session is not None
        self.session.commit()

    def rollback(self) -> None:
        assert self.session is not None
        self.session.rollback()
