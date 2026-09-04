from app.domain.json_types import (
    FrozenJsonObject,
    FrozenJsonValue as _FrozenJsonValue,
    freeze_json_object as _freeze_json_object,
)
from dataclasses import dataclass as _dataclass
from datetime import datetime as _datetime
from collections.abc import Iterator as _Iterator, Mapping as _Mapping
from typing import Protocol as _Protocol

from app.domain.accounts import AccountActor


@_dataclass(frozen=True)
class TaskPayload(_Mapping[str, _FrozenJsonValue]):
    values: FrozenJsonObject

    @classmethod
    def from_mapping(cls, values: FrozenJsonObject | None = None) -> "TaskPayload":
        return cls(_freeze_json_object(values or {}))

    def __getitem__(self, key: str) -> _FrozenJsonValue:
        return self.values[key]

    def __iter__(self) -> _Iterator[str]:
        return iter(self.values)

    def __len__(self) -> int:
        return len(self.values)


@_dataclass(frozen=True)
class TaskLog:
    status: str
    at: str
    message: str | None = None


@_dataclass(frozen=True)
class TaskRecord:
    task_id: str
    task_type: str
    status: str
    progress: float
    input_payload: TaskPayload
    result_payload: TaskPayload
    result_reference: str | None
    error_code: str | None
    error_message: str | None
    created_by: str | None
    attempt_count: int
    logs: tuple[TaskLog, ...]
    created_at: _datetime | None
    updated_at: _datetime | None
    started_at: _datetime | None
    finished_at: _datetime | None

    def __getitem__(self, key: str) -> _FrozenJsonValue:
        """Read-only compatibility access for legacy in-process callers."""
        fields: dict[str, _FrozenJsonValue] = {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "status": "completed" if self.status == "succeeded" else self.status,
            "canonical_status": self.status,
            "progress": self.progress,
            "input_payload": dict(self.input_payload),
            "result_payload": dict(self.result_payload),
            "result_reference": self.result_reference,
            "implementation_status": "database_persisted_sync_executor",
            "execution_mode": "synchronous_local",
            "capability_implementation_status": self.result_payload.get("implementation_status"),
        }
        if key in fields:
            return fields[key]
        return self.result_payload[key]


class TaskRepository(_Protocol):
    def add(self, record: TaskRecord) -> None: ...
    def get(self, task_id: str) -> TaskRecord | None: ...
    def list(self) -> list[TaskRecord]: ...
    def save(self, record: TaskRecord) -> None: ...


class TaskUnitOfWork(_Protocol):
    tasks: TaskRepository

    def __enter__(self) -> "TaskUnitOfWork": ...
    def __exit__(self, exc_type, exc, traceback) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class TaskWorkflowPort(_Protocol):
    """Stable task workflow used by other bounded contexts."""

    def prepare_succeeded(
        self,
        actor: AccountActor,
        task_type: str,
        *,
        input_payload: TaskPayload | None = None,
        result_payload: TaskPayload | None = None,
        result_reference: str | None = None,
        task_id: str | None = None,
    ) -> TaskRecord: ...

    def create_succeeded(
        self,
        actor: AccountActor,
        task_type: str,
        *,
        input_payload: TaskPayload | None = None,
        result_payload: TaskPayload | None = None,
        result_reference: str | None = None,
        task_id: str | None = None,
    ) -> TaskRecord: ...

    def get(
        self,
        actor: AccountActor,
        task_id: str,
        expected_types: set[str] | None = None,
    ) -> TaskRecord: ...


__all__ = [
    "AccountActor",
    "FrozenJsonObject",
    "TaskLog",
    "TaskPayload",
    "TaskRecord",
    "TaskRepository",
    "TaskUnitOfWork",
    "TaskWorkflowPort",
]
