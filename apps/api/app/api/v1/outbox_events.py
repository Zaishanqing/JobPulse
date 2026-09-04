from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies.accounts import get_account_actor
from app.api.dependencies.use_cases import get_outbox_event_use_cases
from app.contexts.platform import (
    ManageOutboxEvents,
    OutboxEventNotFound,
    OutboxRequeueConflict,
)
from app.core.response import success_response
from app.domain.accounts import AccountActor


router = APIRouter(prefix="/outbox-events", tags=["outbox-events"])


@router.post("/{event_id}/requeue")
def requeue_outbox_event_api(
    event_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageOutboxEvents = Depends(get_outbox_event_use_cases),
):
    try:
        record = use_cases.requeue(actor, event_id)
    except OutboxEventNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OutboxRequeueConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return success_response(
        data={
            "event_id": record.draft.event.event_id,
            "status": record.status.value,
            "attempts": record.attempts,
            "next_attempt_at": record.next_attempt_at.isoformat(),
        }
    )
