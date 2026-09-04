from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

from app.domain.json_types import FrozenJsonObject


class OutboxStatus(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    DELIVERED = "delivered"
    RETRYABLE = "retryable"
    DEAD_LETTER = "dead_letter"


@dataclass(frozen=True)
class IdempotencyKey:
    value: str


@dataclass(frozen=True)
class IntegrationEvent:
    event_id: str
    event_type: str
    aggregate_id: str
    payload: FrozenJsonObject
    occurred_at: datetime
    trace_id: str | None = None


@dataclass(frozen=True)
class OutboxMessageDraft:
    event: IntegrationEvent
    idempotency_key: IdempotencyKey


@dataclass(frozen=True)
class DeliveryAttempt:
    number: int
    attempted_at: datetime
    error: str | None = None


@dataclass(frozen=True)
class OutboxMessageRecord:
    message_id: str
    draft: OutboxMessageDraft
    status: OutboxStatus
    attempts: int
    next_attempt_at: datetime
    lease_owner: str | None = None
    lease_until: datetime | None = None
    last_error: str | None = None


@dataclass(frozen=True)
class DispatchResult:
    delivered: bool
    retryable: bool = False
    error: str | None = None


class OutboxRepository(Protocol):
    def add(self, draft: OutboxMessageDraft) -> OutboxMessageRecord: ...
    def claim(self, worker_id: str, now: datetime) -> OutboxMessageRecord | None: ...
    def complete(self, message_id: str, worker_id: str, result: DispatchResult) -> bool: ...


class IntegrationEventHandler(Protocol):
    def handle(self, event: IntegrationEvent, idempotency_key: IdempotencyKey) -> DispatchResult: ...


class OutboxDispatcher(Protocol):
    def dispatch_one(self, worker_id: str, now: datetime) -> DispatchResult | None: ...
