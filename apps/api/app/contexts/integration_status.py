from __future__ import annotations

from typing import Callable, Protocol

from app.domain.accounts import AccountActor
from app.domain.json_types import FrozenJsonArray, FrozenJsonObject
from app.domain.permissions import require_permission


class IntegrationStatusReader(Protocol):
    def get(
        self,
        *,
        actor_role: str,
        jd_id: str | None,
        cv_task_id: str | None,
        trend_task_id: str | None = None,
    ) -> FrozenJsonObject: ...

    def list_demo_tasks(
        self,
        *,
        task_type: str | None,
        status: str | None,
        object_id: str | None,
    ) -> FrozenJsonArray: ...


class QueryIntegrationStatus:
    def __init__(self, reader_factory: Callable[[], IntegrationStatusReader]) -> None:
        self._reader_factory = reader_factory

    def get(
        self,
        actor: AccountActor,
        *,
        jd_id: str | None = None,
        cv_task_id: str | None = None,
        trend_task_id: str | None = None,
    ) -> FrozenJsonObject:
        require_permission(actor.role, "integration.status.view")
        if not jd_id and not cv_task_id and not trend_task_id:
            raise ValueError("jd_id, cv_task_id or trend_task_id is required")
        reader = self._reader_factory()
        if trend_task_id:
            return reader.get(
                actor_role=actor.role,
                jd_id=jd_id,
                cv_task_id=cv_task_id,
                trend_task_id=trend_task_id,
            )
        return reader.get(
            actor_role=actor.role,
            jd_id=jd_id,
            cv_task_id=cv_task_id,
        )

    def list_demo_tasks(
        self,
        actor: AccountActor,
        *,
        task_type: str | None = None,
        status: str | None = None,
        object_id: str | None = None,
    ) -> FrozenJsonArray:
        require_permission(actor.role, "integration.status.view")
        return self._reader_factory().list_demo_tasks(
            task_type=task_type,
            status=status,
            object_id=object_id,
        )
