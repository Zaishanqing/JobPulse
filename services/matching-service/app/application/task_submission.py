"""API-side orchestration: persist a task, then publish only its lightweight identity."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from uuid import uuid4

from app.application.evaluation_tasks import EvaluationTaskService
from app.application.outbox_dispatcher import OutboxDispatcher
from app.domain.outbox import outbox_id_for_task
from app.domain.tasks import TaskSubmissionResult
from app.ports.observability import MetricsCollector, NullMetricsCollector
from app.ports.task_queue import TaskQueue


class TaskSubmissionService:
    def __init__(
        self,
        tasks: EvaluationTaskService,
        queue: TaskQueue,
        *,
        clock: Callable[[], datetime] | None = None,
        metrics: MetricsCollector | None = None,
        retry_interval_seconds: float = 5,
    ) -> None:
        self._tasks = tasks
        self._queue = queue
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._metrics = metrics or NullMetricsCollector()
        self._dispatcher = OutboxDispatcher(
            tasks.unit_of_work_factory,
            queue,
            dispatcher_id=f"api-dispatcher-{uuid4().hex}",
            retry_interval_seconds=retry_interval_seconds,
            clock=tasks.clock,
        )

    def submit(
        self,
        payload: object,
        idempotency_key: str,
        access_scope: str,
        tenant_ref: str | None = None,
    ) -> TaskSubmissionResult:
        result = self._tasks.submit(
            payload,
            idempotency_key,
            access_scope,
            execute_immediately=False,
            create_outbox=True,
            tenant_ref=tenant_ref,
        )
        task = result.task
        if task is None or result.error_code is not None:
            return result
        if task.status == "succeeded" or task.attempt >= task.max_attempts:
            return result
        dispatched = self._dispatcher.dispatch_once(outbox_id_for_task(task.task_id))
        if dispatched.outcome == "retried":
            self._metrics.increment(
                "matching_dependency_errors_total", component="redis"
            )
            return result.model_copy(
                update={
                    "error_code": dispatched.reason_code,
                    "error_message": "task persisted; outbox dispatch will retry",
                }
            )
        return result
