"""Message queue Port; implementations own delivery, never matching rules."""

from __future__ import annotations

from typing import Protocol

from app.domain.queue import QueueDelivery, TaskQueueMessage


class TaskQueueError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class TaskQueue(Protocol):
    def publish(self, message: TaskQueueMessage) -> None: ...

    def consume(self, worker_id: str) -> QueueDelivery | None: ...

    def acknowledge(self, delivery: QueueDelivery) -> None: ...

    def retry(
        self, delivery: QueueDelivery, *, delay_seconds: float, reason_code: str
    ) -> None: ...

    def dead_letter(self, delivery: QueueDelivery, *, reason_code: str) -> None: ...
