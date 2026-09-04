from __future__ import annotations

import io
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from threading import Event, Thread, Timer

from fastapi.testclient import TestClient

from app.application.evaluation import MatchEvaluationService
from app.application.evaluation_tasks import EvaluationTaskService
from app.application.health import DependencySpec, HealthService
from app.application.learning_paths import LearningPathService
from app.application.task_submission import TaskSubmissionService
from app.application.task_worker import EvaluationTaskWorker
from app.bootstrap.application import create_app
from app.domain.vector_contracts import VectorContractViolation
from app.infrastructure.memory_repositories import InMemoryPersistence
from app.infrastructure.memory_task_queue import InMemoryTaskQueue
from app.infrastructure.metrics import MetricsRegistry
from app.infrastructure.structured_logging import StructuredLogger
from app.infrastructure.worker_monitoring import WorkerMonitoringServer


class _Healthy:
    def __init__(self) -> None:
        self.calls = 0

    def check_health(self) -> None:
        self.calls += 1


class _HealthyModel(_Healthy):
    artifact_digest = "a" * 64


class _Unhealthy:
    def __init__(self, code: str) -> None:
        self.code = code

    def check_health(self) -> None:
        raise VectorContractViolation(self.code, "dependency contains secret@example.com")


def test_readiness_ignores_disabled_memory_and_checks_external_dependencies():
    metrics = MetricsRegistry()
    postgres = _Healthy()
    redis = _Healthy()
    service = HealthService(
        (
            DependencySpec("postgresql", "postgres", postgres),
            DependencySpec("redis", "redis", redis),
            DependencySpec("embedding", "disabled"),
            DependencySpec("vector", "memory"),
        ),
        metrics,
    )

    report = service.readiness()

    assert report.status == "ready"
    assert postgres.calls == redis.calls == 1
    by_name = {item.component: item for item in report.components}
    assert by_name["postgresql"].status == "ready"
    assert by_name["redis"].status == "ready"
    assert by_name["embedding"].status == "disabled"
    assert by_name["vector"].status == "disabled"


def test_readiness_reports_verified_responsibility_ce_digest():
    service = HealthService(
        (DependencySpec("responsibility_ce", "model", _HealthyModel()),),
        MetricsRegistry(),
    )

    report = service.readiness()

    assert report.status == "ready"
    component = report.components[0]
    assert component.status == "ready"
    assert component.required is True
    assert component.artifact_digest == "a" * 64


def test_readiness_failure_has_stable_component_code_and_metric():
    metrics = MetricsRegistry()
    service = HealthService(
        (
            DependencySpec(
                "postgresql", "postgres", _Unhealthy("POSTGRES_UNAVAILABLE")
            ),
            DependencySpec("redis", "redis", _Unhealthy("REDIS_UNAVAILABLE")),
            DependencySpec("embedding", "http", _Unhealthy("EMBEDDING_TIMEOUT")),
            DependencySpec("vector", "http", _Unhealthy("VECTOR_UNAVAILABLE")),
        ),
        metrics,
    )

    report = service.readiness()

    assert report.status == "not_ready"
    errors = {item.component: item.error_code for item in report.components}
    assert errors["redis"] == "REDIS_UNAVAILABLE"
    assert errors["embedding"] == "EMBEDDING_TIMEOUT"
    assert errors["postgresql"] == "POSTGRES_UNAVAILABLE"
    assert errors["vector"] == "VECTOR_UNAVAILABLE"
    assert metrics.counter_value(
        "matching_dependency_errors_total", component="redis"
    ) == 1
    assert metrics.counter_value(
        "matching_dependency_errors_total", component="embedding"
    ) == 1
    assert metrics.counter_value(
        "matching_dependency_errors_total", component="postgresql"
    ) == 1
    assert metrics.counter_value(
        "matching_dependency_errors_total", component="vector"
    ) == 1


def test_health_endpoints_distinguish_live_ready_and_dependency_failure():
    application = create_app()
    client = TestClient(application)

    live = client.get("/health/live")
    ready = client.get("/health/ready")

    assert live.status_code == 200
    assert live.json()["data"]["status"] == "alive"
    assert ready.status_code == 200
    assert ready.json()["data"]["status"] == "ready"
    assert all(
        item["status"] == "disabled" for item in ready.json()["data"]["components"]
    )

    application.state.health_service = HealthService(
        (
            DependencySpec("postgresql", "postgres", _Unhealthy("POSTGRES_UNAVAILABLE")),
            DependencySpec("redis", "memory"),
            DependencySpec("embedding", "disabled"),
            DependencySpec("vector", "memory"),
        ),
        application.state.metrics_registry,
    )
    failed = client.get("/health/ready")
    assert failed.status_code == 503
    assert failed.json()["data"]["status"] == "not_ready"
    assert failed.json()["data"]["components"][0]["error_code"] == (
        "POSTGRES_UNAVAILABLE"
    )


def _capturing_logger() -> tuple[StructuredLogger, io.StringIO]:
    stream = io.StringIO()
    logger = logging.getLogger(f"matching-test-{id(stream)}")
    logger.handlers = [logging.StreamHandler(stream)]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return StructuredLogger(logger), stream


def test_structured_logging_allowlist_filters_sensitive_values():
    logger, stream = _capturing_logger()
    rendered = logger.event(
        "security_test",
        request_id="request-safe",
        access_scope="person@example.com",
        task_id="task-safe",
        cv_text="private CV text",
        vector=[0.1, 0.2],
        api_key="secret-api-key",
    )

    payload = json.loads(rendered)
    assert payload["request_id"] == "request-safe"
    assert payload["access_scope"] == "[redacted]"
    assert "private CV text" not in stream.getvalue()
    assert "0.1" not in stream.getvalue()
    assert "secret-api-key" not in stream.getvalue()
    assert "cv_text" not in payload
    assert "vector" not in payload
    assert "api_key" not in payload


def test_request_ids_propagate_and_http_logs_never_include_authorization():
    logger, stream = _capturing_logger()
    application = create_app(structured_logger=logger)
    client = TestClient(application)
    response = client.get(
        "/health/live",
        headers={
            "X-Request-ID": "request-123",
            "X-Correlation-ID": "correlation-456",
            "Authorization": "Bearer top-secret-token",
        },
    )

    assert response.headers["X-Request-ID"] == "request-123"
    assert response.headers["X-Correlation-ID"] == "correlation-456"
    logged = stream.getvalue()
    assert '"request_id":"request-123"' in logged
    assert '"correlation_id":"correlation-456"' in logged
    assert "top-secret-token" not in logged


def test_metrics_accumulate_requests_task_states_and_durations(
    ready_cv_json, ready_position_json, auth_provider, auth_headers
):
    application = create_app(authentication_provider=auth_provider)
    client = TestClient(application)
    client.get("/health/live")
    client.get("/health/live")
    client.post(
        "/api/v1/evaluation-tasks",
        json={"cv_profile": ready_cv_json, "position_profile": ready_position_json},
        headers={**auth_headers, "Idempotency-Key": "metrics-task"},
    )

    body = client.get("/metrics").text

    assert "matching_http_requests_total" in body
    assert 'path="/health/live"' in body
    assert "matching_http_request_duration_seconds_count" in body
    assert 'matching_tasks{status="pending"} 1' in body
    assert 'matching_tasks{status="running"} 0' in body


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 27, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


class _BlockingEvaluation:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.delegate = MatchEvaluationService()

    def evaluate(self, payload: object, **kwargs):
        self.started.set()
        self.release.wait(2)
        return self.delegate.evaluate(payload, **kwargs)


def _blocking_worker(cv: dict, position: dict, clock: _Clock, worker_auth_context):
    evaluation = _BlockingEvaluation()
    storage = InMemoryPersistence()
    tasks = EvaluationTaskService(
        storage.unit_of_work,
        evaluation,
        LearningPathService(evaluation),
    )
    queue = InMemoryTaskQueue(visibility_timeout_seconds=10, clock=clock)
    task = TaskSubmissionService(tasks, queue, clock=clock).submit(
        {"cv_profile": cv, "position_profile": position},
        "shutdown-task",
        "tenant-a",
    ).task
    worker = EvaluationTaskWorker(
        queue, tasks, worker_id="shutdown-worker", auth_context=worker_auth_context
    )
    return evaluation, tasks, queue, task, worker


def test_worker_graceful_shutdown_allows_current_task_to_finish(
    ready_cv_json, ready_position_json, worker_auth_context
):
    clock = _Clock()
    evaluation, tasks, queue, task, worker = _blocking_worker(
        ready_cv_json, ready_position_json, clock, worker_auth_context
    )
    result = []
    thread = Thread(target=lambda: result.append(worker.run_once()))
    thread.start()
    assert evaluation.started.wait(1)
    Timer(0.05, evaluation.release.set).start()

    assert worker.shutdown(1) is True
    thread.join(1)
    assert result[0].outcome == "acknowledged"
    assert queue.inflight_count == 0
    assert tasks.get_task(task.task_id, "tenant-a").task.status == "succeeded"


def test_worker_shutdown_timeout_leaves_message_unacknowledged_for_recovery(
    ready_cv_json, ready_position_json, worker_auth_context
):
    clock = _Clock()
    evaluation, tasks, queue, task, worker = _blocking_worker(
        ready_cv_json, ready_position_json, clock, worker_auth_context
    )
    result = []
    thread = Thread(target=lambda: result.append(worker.run_once()))
    thread.start()
    assert evaluation.started.wait(1)

    assert worker.shutdown(0.01) is False
    evaluation.release.set()
    thread.join(1)
    assert result[0].outcome == "abandoned"
    assert queue.inflight_count == 1

    clock.advance(11)
    replacement = EvaluationTaskWorker(
        queue, tasks, worker_id="replacement-worker", auth_context=worker_auth_context
    )
    assert replacement.run_once().outcome == "acknowledged"
    assert queue.inflight_count == 0
    assert tasks.get_task(task.task_id, "tenant-a").task.status == "succeeded"


def test_api_shutdown_gate_rejects_new_work_but_keeps_liveness():
    application = create_app()
    client = TestClient(application)
    application.state.api_runtime.accepting_requests = False

    assert client.get("/health/live").status_code == 200
    rejected = client.get("/health/ready")
    assert rejected.status_code == 503
    assert rejected.json()["code"] == "SERVICE_SHUTTING_DOWN"


class _AlwaysFail:
    def evaluate(self, payload: object, **kwargs):
        raise RuntimeError("worker failure")


def test_worker_retry_dead_letter_and_duration_metrics(
    ready_cv_json, ready_position_json, worker_auth_context
):
    storage = InMemoryPersistence()
    evaluation = _AlwaysFail()
    tasks = EvaluationTaskService(
        storage.unit_of_work,
        evaluation,
        LearningPathService(evaluation),
        max_attempts=2,
    )
    queue = InMemoryTaskQueue()
    TaskSubmissionService(tasks, queue).submit(
        {"cv_profile": ready_cv_json, "position_profile": ready_position_json},
        "worker-metrics",
        "tenant-a",
    )
    metrics = MetricsRegistry()
    worker = EvaluationTaskWorker(
        queue,
        tasks,
        worker_id="metrics-worker",
        retry_interval_seconds=0,
        metrics=metrics,
        auth_context=worker_auth_context,
    )

    assert worker.run_once().outcome == "retried"
    assert worker.run_once().outcome == "dead_lettered"
    assert metrics.counter_value("matching_worker_retries_total") == 1
    assert metrics.counter_value("matching_worker_dead_letters_total") == 1
    rendered = metrics.render()
    assert "matching_worker_execution_duration_seconds_count 2" in rendered


def test_independent_worker_monitoring_exposes_metrics_live_and_ready(
    worker_auth_context,
):
    storage = InMemoryPersistence()
    evaluation = MatchEvaluationService()
    metrics = MetricsRegistry()
    tasks = EvaluationTaskService(
        storage.unit_of_work,
        evaluation,
        LearningPathService(evaluation),
    )
    worker = EvaluationTaskWorker(
        InMemoryTaskQueue(),
        tasks,
        worker_id="monitored-worker",
        metrics=metrics,
        auth_context=worker_auth_context,
    )
    metrics.increment("matching_worker_retries_total")
    server = WorkerMonitoringServer(
        metrics, worker, host="127.0.0.1", port=0
    )
    server.start()
    base = f"http://127.0.0.1:{server.port}"
    try:
        with urllib.request.urlopen(f"{base}/health/live", timeout=2) as response:
            assert response.status == 200
        with urllib.request.urlopen(f"{base}/health/ready", timeout=2) as response:
            assert response.status == 200
        with urllib.request.urlopen(f"{base}/metrics", timeout=2) as response:
            assert "matching_worker_retries_total 1" in response.read().decode()
        worker.request_shutdown()
        try:
            urllib.request.urlopen(f"{base}/health/ready", timeout=2)
        except urllib.error.HTTPError as exc:
            assert exc.code == 503
        else:
            raise AssertionError("stopping worker readiness must return 503")
    finally:
        server.stop()
