from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies.accounts import get_account_actor
from app.api.dependencies.tasks import get_task_use_cases
from app.api.task_mapping import task_data
from app.contexts.tasks import ManageTasks, TaskNotFound
from app.core.response import success_response
from app.domain.accounts import AccountActor
from app.domain.tasks import TaskTransitionConflict
from app.domain.errors import PermissionDenied


router = APIRouter(prefix="/tasks", tags=["tasks"])


def _raise(exc: Exception) -> None:
    if isinstance(exc, TaskNotFound):
        code = 404
    elif isinstance(exc, PermissionDenied):
        code = 403
    elif isinstance(exc, TaskTransitionConflict):
        code = 409
    else:
        code = 400
    raise HTTPException(status_code=code, detail=str(exc)) from exc


@router.get("")
def list_tasks(actor: AccountActor = Depends(get_account_actor), use_cases: ManageTasks = Depends(get_task_use_cases)):
    try:
        items = use_cases.list(actor)
    except PermissionDenied as exc:
        _raise(exc)
    return success_response(data=[task_data(item) for item in items])


@router.get("/{task_id}")
def get_task(task_id: str, actor: AccountActor = Depends(get_account_actor), use_cases: ManageTasks = Depends(get_task_use_cases)):
    try:
        item = use_cases.get(actor, task_id)
    except (TaskNotFound, PermissionDenied) as exc:
        _raise(exc)
    return success_response(data=task_data(item))


def _transition(task_id: str, target: str, actor: AccountActor, use_cases: ManageTasks):
    try:
        item = use_cases.transition(actor, task_id, target)
    except (TaskNotFound, PermissionDenied, TaskTransitionConflict) as exc:
        _raise(exc)
    return success_response(data=task_data(item))


@router.post("/{task_id}/cancel")
def cancel_task(task_id: str, actor: AccountActor = Depends(get_account_actor), use_cases: ManageTasks = Depends(get_task_use_cases)):
    return _transition(task_id, "cancelled", actor, use_cases)


@router.post("/{task_id}/retry")
def retry_task(task_id: str, actor: AccountActor = Depends(get_account_actor), use_cases: ManageTasks = Depends(get_task_use_cases)):
    return _transition(task_id, "pending", actor, use_cases)


@router.get("/{task_id}/logs")
def get_task_logs(task_id: str, actor: AccountActor = Depends(get_account_actor), use_cases: ManageTasks = Depends(get_task_use_cases)):
    try:
        item = use_cases.get(actor, task_id)
    except (TaskNotFound, PermissionDenied) as exc:
        _raise(exc)
    return success_response(data={"task_id": item.task_id, "logs": [{"status": entry.status, "at": entry.at, "message": entry.message} for entry in item.logs], "implementation_status": "database_persisted_sync_executor"})
