"""Lease-based transactional outbox dispatcher."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from threading import Event

from app.domain.outbox import OutboxDispatchResult
from app.ports.repositories import UnitOfWorkFactory
from app.ports.task_queue import TaskQueue, TaskQueueError


class OutboxDispatcher:
    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        queue: TaskQueue,
        *,
        dispatcher_id: str,
        lease_seconds: float = 30,
        retry_interval_seconds: float = 5,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not dispatcher_id or lease_seconds <= 0 or retry_interval_seconds < 0:
            raise ValueError("dispatcher id, positive lease and non-negative retry are required")
        self._unit_of_work = unit_of_work
        self._queue = queue
        self.dispatcher_id = dispatcher_id
        self.lease_seconds = lease_seconds
        self.retry_interval_seconds = retry_interval_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def dispatch_once(self, outbox_id: str | None = None) -> OutboxDispatchResult:
        now = self._clock()
        with self._unit_of_work() as uow:
            record = uow.outbox.claim(
                self.dispatcher_id,
                now,
                now + timedelta(seconds=self.lease_seconds),
                outbox_id,
            )
            uow.commit()
        if record is None:
            return OutboxDispatchResult(outcome="idle")
        try:
            self._queue.publish(record.payload)
        except TaskQueueError as exc:
            failed_at = self._clock()
            with self._unit_of_work() as uow:
                released = uow.outbox.release_for_retry(
                    record.outbox_id,
                    self.dispatcher_id,
                    failed_at + timedelta(seconds=self.retry_interval_seconds),
                    exc.code,
                    failed_at,
                )
                uow.commit()
            return OutboxDispatchResult(
                outcome="retried" if released is not None else "lost_claim",
                outbox_id=record.outbox_id,
                reason_code=exc.code,
            )
        published_at = self._clock()
        with self._unit_of_work() as uow:
            published = uow.outbox.mark_published(
                record.outbox_id, self.dispatcher_id, published_at
            )
            uow.commit()
        return OutboxDispatchResult(
            outcome="published" if published is not None else "lost_claim",
            outbox_id=record.outbox_id,
            reason_code=None if published is not None else "OUTBOX_CLAIM_LOST",
        )

    def run_forever(
        self, *, stop_event: Event | None = None, idle_sleep_seconds: float = 0.25
    ) -> None:
        stop = stop_event or Event()
        while not stop.is_set():
            result = self.dispatch_once()
            if result.outcome in {"idle", "retried", "lost_claim"}:
                stop.wait(idle_sleep_seconds)
