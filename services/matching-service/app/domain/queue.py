"""Immutable contracts for lightweight, at-least-once task messages."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.domain.privacy import find_pii
from app.domain.profiles import ImmutableDTO


def task_message_id(task_id: str, version_signature: str) -> str:
    """Bind a lightweight message identity to its task and version."""
    return "message_" + task_id


class TaskQueueMessage(ImmutableDTO):
    message_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    access_scope: str = Field(min_length=1)
    version_signature: str = Field(min_length=1)
    published_at: datetime


class QueueDelivery(ImmutableDTO):
    receipt_id: str = Field(min_length=1)
    message: TaskQueueMessage
    worker_id: str = Field(min_length=1)
    delivery_count: int = Field(ge=1)
    leased_until: datetime


class DeadLetterRecord(ImmutableDTO):
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,127}$")
    delivery_count: int = Field(ge=1)
    message_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    task_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    access_scope: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9_.:-]{1,200}$", repr=False
    )
    version_signature: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:=+|/-]{0,1999}$"
    )
    timestamp: datetime

    @model_validator(mode="after")
    def _pii_guard(self) -> DeadLetterRecord:
        if find_pii(self.model_dump(mode="python")):
            raise ValueError("DLQ_ENVELOPE_UNSAFE")
        return self


def anonymize_access_scope(access_scope: str) -> str:
    kind = access_scope.split(":", 1)[0] if ":" in access_scope else "scope"
    return f"{kind}:redacted"


def dead_letter_for_delivery(
    delivery: QueueDelivery, reason_code: str, occurred_at: datetime
) -> DeadLetterRecord:
    message = delivery.message
    return DeadLetterRecord(
        reason_code=reason_code,
        delivery_count=delivery.delivery_count,
        message_id=message.message_id,
        task_id=message.task_id,
        access_scope=anonymize_access_scope(message.access_scope),
        version_signature=message.version_signature,
        timestamp=occurred_at,
    )


class WorkerRunResult(ImmutableDTO):
    outcome: Literal["idle", "acknowledged", "retried", "dead_lettered", "abandoned"]
    task_id: str | None = None
    reason_code: str | None = None
