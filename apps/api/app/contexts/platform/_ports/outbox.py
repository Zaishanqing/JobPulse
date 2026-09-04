from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.integration_events import OutboxMessageRecord


class OutboxEventNotFound(LookupError):
    pass


class OutboxRequeueConflict(ValueError):
    pass


class OutboxOperationsRepository(Protocol):
    def requeue(self, event_id: str, now: datetime) -> OutboxMessageRecord: ...


class OutboxOperationsUnitOfWork(Protocol):
    outbox: OutboxOperationsRepository

    def __enter__(self) -> "OutboxOperationsUnitOfWork": ...
    def __exit__(self, exc_type, exc, traceback) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
