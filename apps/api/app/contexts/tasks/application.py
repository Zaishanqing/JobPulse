from dataclasses import dataclass as _dataclass, replace as _replace
from typing import Callable as _Callable
from uuid import uuid4 as _uuid4

from app.domain.accounts import AccountActor
from app.domain.tasks import (
    INTERNAL_TASK_ROLES as _INTERNAL_TASK_ROLES,
    TERMINAL_TASK_STATUSES as _TERMINAL_TASK_STATUSES,
    require_transition as _require_transition,
    utc_now as _utc_now,
)
from app.contexts.tasks.ports import TaskLog, TaskPayload, TaskRecord, TaskUnitOfWork
from app.domain.errors import PermissionDenied


class TaskNotFound(LookupError):
    pass


@_dataclass(frozen=True)
class ManageTasks:
    uow_factory: _Callable[[], TaskUnitOfWork]

    def create_succeeded(
        self,
        actor: AccountActor,
        task_type: str,
        *,
        input_payload: TaskPayload | None = None,
        result_payload: TaskPayload | None = None,
        result_reference: str | None = None,
        task_id: str | None = None,
    ) -> TaskRecord:
        record = self.prepare_succeeded(
            actor,
            task_type,
            input_payload=input_payload,
            result_payload=result_payload,
            result_reference=result_reference,
            task_id=task_id,
        )
        with self.uow_factory() as uow:
            uow.tasks.add(record)
            uow.commit()
        return record

    def prepare_succeeded(
        self,
        actor: AccountActor,
        task_type: str,
        *,
        input_payload: TaskPayload | None = None,
        result_payload: TaskPayload | None = None,
        result_reference: str | None = None,
        task_id: str | None = None,
    ) -> TaskRecord:
        return build_succeeded_task(
            actor,
            task_type,
            input_payload=input_payload,
            result_payload=result_payload,
            result_reference=result_reference,
            task_id=task_id,
        )

    def get(
        self, actor: AccountActor, task_id: str, expected_types: set[str] | None = None
    ) -> TaskRecord:
        with self.uow_factory() as uow:
            record = uow.tasks.get(task_id)
            if (
                record is None
                or expected_types is not None
                and record.task_type not in expected_types
            ):
                raise TaskNotFound("Task not found")
            self._access(actor, record)
            return record

    def list(self, actor: AccountActor) -> list[TaskRecord]:
        if actor.role not in _INTERNAL_TASK_ROLES:
            raise PermissionDenied("Permission denied")
        with self.uow_factory() as uow:
            return uow.tasks.list()

    def transition(self, actor: AccountActor, task_id: str, target: str) -> TaskRecord:
        with self.uow_factory() as uow:
            current = uow.tasks.get(task_id)
            if current is None:
                raise TaskNotFound("Task not found")
            self._access(actor, current)
            _require_transition(current.status, target)
            now = _utc_now()
            logs = (
                *current.logs,
                TaskLog(
                    target,
                    now.isoformat(),
                    "Cancelled by user" if target == "cancelled" else "Queued for retry",
                ),
            )
            record = _replace(
                current,
                status=target,
                progress=0.0 if target == "pending" else current.progress,
                attempt_count=current.attempt_count + (1 if target == "pending" else 0),
                result_payload=TaskPayload.from_mapping()
                if target == "pending"
                else current.result_payload,
                result_reference=None if target == "pending" else current.result_reference,
                error_code=None,
                error_message=None,
                logs=logs,
                updated_at=now,
                finished_at=now if target in _TERMINAL_TASK_STATUSES else None,
            )
            uow.tasks.save(record)
            uow.commit()
            return record

    @staticmethod
    def _access(actor: AccountActor, record: TaskRecord) -> None:
        if record.created_by != actor.account_id and actor.role not in _INTERNAL_TASK_ROLES:
            raise PermissionDenied("No permission to access this task")


def build_succeeded_task(
    actor: AccountActor,
    task_type: str,
    *,
    input_payload: TaskPayload | None = None,
    result_payload: TaskPayload | None = None,
    result_reference: str | None = None,
    task_id: str | None = None,
) -> TaskRecord:
    now = _utc_now()
    logs = (
        TaskLog("pending", now.isoformat()),
        TaskLog("running", now.isoformat()),
        TaskLog("succeeded", now.isoformat(), "Completed by synchronous local executor"),
    )
    return TaskRecord(
        task_id or f"{task_type}_{_uuid4()}",
        task_type,
        "succeeded",
        1.0,
        input_payload or TaskPayload.from_mapping(),
        result_payload or TaskPayload.from_mapping(),
        result_reference,
        None,
        None,
        actor.account_id,
        1,
        logs,
        now,
        now,
        now,
        now,
    )


__all__ = [
    "AccountActor",
    "ManageTasks",
    "PermissionDenied",
    "TaskLog",
    "TaskNotFound",
    "TaskPayload",
    "TaskRecord",
    "TaskUnitOfWork",
    "build_succeeded_task",
]
