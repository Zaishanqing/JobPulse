"""Transactional outbox contracts independent of persistence and Redis."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.domain.profiles import ImmutableDTO
from app.domain.queue import TaskQueueMessage


def outbox_id_for_task(task_id: str) -> str:
    return "outbox_" + task_id


class OutboxRecord(ImmutableDTO):
    outbox_id: str = Field(min_length=1, max_length=64)
    access_scope: str = Field(min_length=1, max_length=200, exclude=True, repr=False)
    task_id: str = Field(min_length=1, max_length=64)
    message_id: str = Field(min_length=1, max_length=64)
    payload: TaskQueueMessage = Field(exclude=True, repr=False)
    status: Literal["pending", "claimed", "published"] = "pending"
    attempt: int = Field(default=0, ge=0)
    available_at: datetime
    claimed_by: str | None = Field(default=None, min_length=1, max_length=200)
    claim_expires_at: datetime | None = None
    published_at: datetime | None = None
    last_error_code: str | None = Field(default=None, max_length=200)
    created_at: datetime
    updated_at: datetime


class OutboxDispatchResult(ImmutableDTO):
    outcome: Literal["idle", "published", "retried", "lost_claim"]
    outbox_id: str | None = None
    reason_code: str | None = None
