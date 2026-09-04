from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from app.contexts.platform._ports.outbox import (
    OutboxEventNotFound as OutboxEventNotFound,
    OutboxOperationsUnitOfWork,
    OutboxRequeueConflict as OutboxRequeueConflict,
)
from app.domain.accounts import AccountActor
from app.domain.permissions import INTEGRATION_OUTBOX_REQUEUE, require_permission
from app.integration_events import OutboxMessageRecord


@dataclass(frozen=True)
class ManageOutboxEvents:
    uow_factory: Callable[[], OutboxOperationsUnitOfWork]

    def requeue(
        self,
        actor: AccountActor,
        event_id: str,
        *,
        now: datetime | None = None,
    ) -> OutboxMessageRecord:
        require_permission(actor.role, INTEGRATION_OUTBOX_REQUEUE)
        requested_at = now or datetime.now(timezone.utc)
        with self.uow_factory() as uow:
            record = uow.outbox.requeue(event_id, requested_at)
            uow.commit()
            return record
