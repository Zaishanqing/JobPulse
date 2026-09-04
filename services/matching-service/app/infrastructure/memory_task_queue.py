"""In-memory at-least-once queue with visibility timeout and dead letters."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock

from app.domain.privacy import find_pii
from app.domain.queue import (
    DeadLetterRecord,
    QueueDelivery,
    TaskQueueMessage,
    dead_letter_for_delivery,
)
from app.ports.task_queue import TaskQueueError


@dataclass
class _Pending:
    message: TaskQueueMessage
    available_at: datetime
    delivery_count: int


@dataclass
class _InFlight:
    delivery: QueueDelivery


class InMemoryTaskQueue:
    def __init__(
        self,
        *,
        visibility_timeout_seconds: float = 60.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if visibility_timeout_seconds <= 0:
            raise ValueError("visibility_timeout_seconds must be positive")
        self.visibility_timeout_seconds = visibility_timeout_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._pending: deque[_Pending] = deque()
        self._inflight: dict[str, _InFlight] = {}
        self._dead_letters: dict[str, DeadLetterRecord] = {}
        self._receipt_sequence = 0
        self._lock = RLock()

    def publish(self, message: TaskQueueMessage) -> None:
        if find_pii(message.model_dump(mode="python")):
            raise TaskQueueError(
                "QUEUE_MESSAGE_PII_FORBIDDEN",
                "queue message contains prohibited PII",
                retryable=False,
            )
        with self._lock:
            self._pending.append(_Pending(message, self._clock(), 0))

    def consume(self, worker_id: str) -> QueueDelivery | None:
        if not worker_id:
            raise TaskQueueError(
                "QUEUE_WORKER_ID_INVALID", "worker_id is required", retryable=False
            )
        with self._lock:
            now = self._clock()
            self._reclaim_expired(now)
            selected = self._take_available(now)
            if selected is None:
                return None
            self._receipt_sequence += 1
            receipt = f"memory-receipt-{self._receipt_sequence}"
            delivery = QueueDelivery(
                receipt_id=receipt,
                message=selected.message,
                worker_id=worker_id,
                delivery_count=selected.delivery_count + 1,
                leased_until=now + timedelta(seconds=self.visibility_timeout_seconds),
            )
            self._inflight[receipt] = _InFlight(delivery)
            return delivery

    def acknowledge(self, delivery: QueueDelivery) -> None:
        with self._lock:
            self._pop_delivery(delivery)

    def retry(
        self, delivery: QueueDelivery, *, delay_seconds: float, reason_code: str
    ) -> None:
        if delay_seconds < 0:
            raise TaskQueueError(
                "QUEUE_RETRY_DELAY_INVALID", "retry delay cannot be negative", retryable=False
            )
        if not reason_code:
            raise TaskQueueError(
                "QUEUE_REASON_CODE_REQUIRED", "reason_code is required", retryable=False
            )
        with self._lock:
            current = self._pop_delivery(delivery)
            self._pending.append(
                _Pending(
                    current.message,
                    self._clock() + timedelta(seconds=delay_seconds),
                    current.delivery_count,
                )
            )

    def dead_letter(self, delivery: QueueDelivery, *, reason_code: str) -> None:
        if not reason_code:
            raise TaskQueueError(
                "QUEUE_REASON_CODE_REQUIRED", "reason_code is required", retryable=False
            )
        with self._lock:
            current = self._pop_delivery(delivery)
            settlement_id = self._settlement_id(current, reason_code)
            self._dead_letters.setdefault(
                settlement_id,
                dead_letter_for_delivery(current, reason_code, self._clock()),
            )

    @property
    def dead_letters(self) -> tuple[DeadLetterRecord, ...]:
        with self._lock:
            return tuple(self._dead_letters.values())

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    @property
    def inflight_count(self) -> int:
        with self._lock:
            return len(self._inflight)

    def _reclaim_expired(self, now: datetime) -> None:
        expired = sorted(
            (
                receipt
                for receipt, item in self._inflight.items()
                if item.delivery.leased_until <= now
            )
        )
        for receipt in expired:
            delivery = self._inflight.pop(receipt).delivery
            self._pending.appendleft(
                _Pending(delivery.message, now, delivery.delivery_count)
            )

    def _take_available(self, now: datetime) -> _Pending | None:
        for _ in range(len(self._pending)):
            item = self._pending.popleft()
            if item.available_at <= now:
                return item
            self._pending.append(item)
        return None

    def _pop_delivery(self, delivery: QueueDelivery) -> QueueDelivery:
        item = self._inflight.get(delivery.receipt_id)
        if item is None or item.delivery != delivery:
            raise TaskQueueError(
                "QUEUE_RECEIPT_NOT_FOUND",
                "delivery receipt is unknown or already settled",
                retryable=False,
            )
        del self._inflight[delivery.receipt_id]
        return item.delivery

    @staticmethod
    def _settlement_id(delivery: QueueDelivery, reason_code: str) -> str:
        return f"{delivery.message.message_id}:{reason_code}"
