"""Independent at-least-once task worker orchestration."""

from __future__ import annotations

import logging
import time
from threading import Event

from app.application.authorization import AuthorizationError, require_worker
from app.application.evaluation_tasks import EvaluationTaskService
from app.application.health import HealthService
from app.domain.auth import AuthContext
from app.domain.queue import QueueDelivery, WorkerRunResult, task_message_id
from app.ports.observability import (
    EventLogger,
    MetricsCollector,
    NullEventLogger,
    NullMetricsCollector,
)
from app.ports.task_queue import TaskQueue, TaskQueueError

LOGGER = logging.getLogger(__name__)


class EvaluationTaskWorker:
    def __init__(
        self,
        queue: TaskQueue,
        tasks: EvaluationTaskService,
        *,
        worker_id: str,
        retry_interval_seconds: float = 5.0,
        metrics: MetricsCollector | None = None,
        structured_logger: EventLogger | None = None,
        health_service: HealthService | None = None,
        auth_context: AuthContext,
        lease_seconds: float | None = None,
    ) -> None:
        if not worker_id:
            raise ValueError("worker_id is required")
        if retry_interval_seconds < 0:
            raise ValueError("retry_interval_seconds cannot be negative")
        self._queue = queue
        self._tasks = tasks
        self.worker_id = worker_id
        self.retry_interval_seconds = retry_interval_seconds
        self._metrics = metrics or NullMetricsCollector()
        self._logger = structured_logger or NullEventLogger()
        self._health_service = health_service
        self.auth_context = auth_context
        self.lease_seconds = lease_seconds or float(
            getattr(queue, "visibility_timeout_seconds", 30)
        )
        if self.lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        actor_id = None
        try:
            require_worker(self.auth_context)
        except AuthorizationError as exc:
            self._logger.event(
                "security_audit",
                actor_id=actor_id,
                auth_decision="denied",
                error_code=exc.code,
            )
            raise
        self._logger.event(
            "security_audit",
            actor_id=actor_id,
            auth_decision="allowed",
            error_code="WORKER_AUTHORIZED",
        )
        self._stopping = Event()
        self._abandon_current = Event()
        self._idle = Event()
        self._idle.set()

    def run_once(self) -> WorkerRunResult:
        if self._stopping.is_set():
            return WorkerRunResult(outcome="idle", reason_code="WORKER_STOPPING")
        started = time.perf_counter()
        try:
            delivery = self._queue.consume(self.worker_id)
        except TaskQueueError:
            self._metrics.increment(
                "matching_dependency_errors_total", component="redis"
            )
            raise
        if delivery is None:
            return WorkerRunResult(outcome="idle")
        if self._stopping.is_set():
            return WorkerRunResult(
                outcome="abandoned",
                task_id=delivery.message.task_id,
                reason_code="WORKER_STOPPING",
            )
        self._idle.clear()
        result: WorkerRunResult | None = None
        try:
            result = self._process(delivery)
            if result.outcome == "retried":
                self._metrics.increment("matching_worker_retries_total")
            elif result.outcome == "dead_lettered":
                self._metrics.increment("matching_worker_dead_letters_total")
            return result
        finally:
            duration = time.perf_counter() - started
            self._metrics.observe("matching_worker_execution_duration_seconds", duration)
            self._logger.event(
                "worker_execution",
                worker_id=self.worker_id,
                task_id=delivery.message.task_id,
                access_scope=delivery.message.access_scope,
                algorithm_version=delivery.message.version_signature,
                duration_ms=round(duration * 1000, 3),
                outcome=result.outcome if result is not None else "failed",
                error_code=(result.reason_code if result is not None else "WORKER_ERROR"),
            )
            self._idle.set()

    def _process(self, delivery: QueueDelivery) -> WorkerRunResult:
        task_result = self._tasks.get_task(
            delivery.message.task_id, delivery.message.access_scope
        )
        task = task_result.task
        if task is None:
            return self._dead_letter(delivery, task_result.error_code or "TASK_NOT_FOUND")
        expected_message_id = task_message_id(
            delivery.message.task_id, delivery.message.version_signature
        )
        if delivery.message.message_id != expected_message_id:
            return self._dead_letter(delivery, "TASK_MESSAGE_IDENTITY_MISMATCH")
        if task.versions.signature != delivery.message.version_signature:
            return self._dead_letter(delivery, "TASK_MESSAGE_VERSION_MISMATCH")
        if task.status == "succeeded":
            duplicate = self._tasks.validate_succeeded_duplicate(
                delivery.message.task_id,
                delivery.message.access_scope,
                delivery.message.version_signature,
            )
            if duplicate.task is None:
                return self._dead_letter(
                    delivery,
                    duplicate.error_code or "TASK_TERMINAL_EVALUATION_MISMATCH",
                )
            if self._abandon_current.is_set():
                return self._abandoned(delivery)
            self._queue.acknowledge(delivery)
            return self._observed(
                WorkerRunResult(outcome="acknowledged", task_id=task.task_id), task
            )
        if task.status == "failed" and task.attempt >= task.max_attempts:
            return self._dead_letter(delivery, "TASK_ATTEMPTS_EXHAUSTED")

        claimed_result = self._tasks.claim(
            task.task_id,
            task.access_scope,
            lease_owner=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        claimed = claimed_result.task
        if claimed is None:
            return self._dead_letter(
                delivery, claimed_result.error_code or "TASK_NOT_FOUND"
            )
        if claimed.status == "failed" and claimed.attempt >= claimed.max_attempts:
            return self._dead_letter(delivery, "TASK_ATTEMPTS_EXHAUSTED")
        if claimed.lease_owner != self.worker_id:
            return self._observed(
                WorkerRunResult(
                    outcome="abandoned",
                    task_id=claimed.task_id,
                    reason_code="TASK_LEASE_HELD",
                ),
                claimed,
            )
        executed_result = self._tasks.execute_claimed(
            claimed.task_id,
            claimed.access_scope,
            lease_owner=self.worker_id,
        )
        executed = executed_result.task
        if executed is None:
            return self._observed(
                WorkerRunResult(
                    outcome="abandoned",
                    task_id=claimed.task_id,
                    reason_code=executed_result.error_code or "TASK_LEASE_NOT_OWNED",
                ),
                claimed,
            )
        if self._abandon_current.is_set():
            return self._abandoned(delivery)
        if executed.status == "succeeded":
            self._queue.acknowledge(delivery)
            return self._observed(
                WorkerRunResult(outcome="acknowledged", task_id=executed.task_id),
                executed,
            )
        if executed.status == "failed" and executed.attempt >= executed.max_attempts:
            return self._dead_letter(delivery, "TASK_ATTEMPTS_EXHAUSTED")
        reason = executed.error_code or "TASK_EXECUTION_INCOMPLETE"
        self._queue.retry(
            delivery,
            delay_seconds=self.retry_interval_seconds,
            reason_code=reason,
        )
        return self._observed(
            WorkerRunResult(
                outcome="retried", task_id=executed.task_id, reason_code=reason
            ),
            executed,
        )

    def shutdown(self, timeout_seconds: float) -> bool:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds cannot be negative")
        self._stopping.set()
        completed = self._idle.wait(timeout_seconds)
        if not completed:
            self._abandon_current.set()
        return completed

    def request_shutdown(self) -> None:
        self._stopping.set()

    @property
    def is_stopping(self) -> bool:
        return self._stopping.is_set()

    @property
    def metrics_registry(self) -> MetricsCollector:
        return self._metrics

    @property
    def readiness_status(self) -> str:
        if self.is_stopping:
            return "not_ready"
        if self._health_service is None:
            return "ready"
        return self._health_service.readiness().status

    def run_forever(
        self,
        *,
        stop_event: Event | None = None,
        idle_sleep_seconds: float = 0.25,
    ) -> None:
        stop = stop_event or Event()
        while not stop.is_set() and not self._stopping.is_set():
            try:
                result = self.run_once()
                if result.outcome == "idle":
                    stop.wait(idle_sleep_seconds)
            except TaskQueueError as exc:
                LOGGER.error("queue error code=%s message=%s", exc.code, exc.message)
                stop.wait(max(self.retry_interval_seconds, idle_sleep_seconds))
            except Exception:
                LOGGER.exception("unexpected worker failure")
                time.sleep(idle_sleep_seconds)

    def _dead_letter(
        self, delivery: QueueDelivery, reason_code: str
    ) -> WorkerRunResult:
        if self._abandon_current.is_set():
            return self._abandoned(delivery)
        self._queue.dead_letter(delivery, reason_code=reason_code)
        result = WorkerRunResult(
            outcome="dead_lettered",
            task_id=delivery.message.task_id,
            reason_code=reason_code,
        )
        self._logger.event(
            "worker_task",
            worker_id=self.worker_id,
            task_id=delivery.message.task_id,
            access_scope=delivery.message.access_scope,
            algorithm_version=delivery.message.version_signature,
            outcome=result.outcome,
            error_code=reason_code,
        )
        return result

    def _abandoned(self, delivery: QueueDelivery) -> WorkerRunResult:
        result = WorkerRunResult(
            outcome="abandoned",
            task_id=delivery.message.task_id,
            reason_code="WORKER_SHUTDOWN_TIMEOUT",
        )
        self._logger.event(
            "worker_task",
            worker_id=self.worker_id,
            task_id=delivery.message.task_id,
            access_scope=delivery.message.access_scope,
            algorithm_version=delivery.message.version_signature,
            outcome=result.outcome,
            error_code=result.reason_code,
        )
        return result

    def _observed(self, result: WorkerRunResult, task: object) -> WorkerRunResult:
        self._logger.event(
            "worker_task",
            worker_id=self.worker_id,
            task_id=result.task_id,
            access_scope=getattr(task, "access_scope", None),
            evaluation_id=getattr(task, "evaluation_id", None),
            algorithm_version=getattr(getattr(task, "versions", None), "signature", None),
            status=getattr(task, "status", None),
            outcome=result.outcome,
            error_code=result.reason_code,
        )
        return result
