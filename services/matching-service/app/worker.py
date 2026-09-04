"""Independent worker entry point: ``python -m app.worker``."""

from __future__ import annotations

import logging
import os
import signal
import socket
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt

from app.application.authorization import require_worker
from app.application.task_worker import EvaluationTaskWorker
from app.bootstrap.application import create_app
from app.infrastructure.worker_monitoring import WorkerMonitoringServer


def build_worker(worker_id: str | None = None) -> EvaluationTaskWorker:
    application = create_app()
    selected_id = worker_id or os.getenv("MATCHING_WORKER_ID") or (
        f"{socket.gethostname()}-{os.getpid()}"
    )
    credential = os.getenv("MATCHING_WORKER_TOKEN", "") or _worker_credential(
        selected_id
    )
    context = application.state.authentication_provider.authenticate(credential)
    require_worker(context)
    return EvaluationTaskWorker(
        application.state.task_queue,
        application.state.evaluation_task_service,
        worker_id=selected_id,
        retry_interval_seconds=application.state.queue_retry_interval_seconds,
        metrics=application.state.metrics_registry,
        structured_logger=application.state.structured_logger,
        health_service=application.state.health_service,
        auth_context=context,
    )


def _worker_credential(worker_id: str) -> str:
    signing_key = os.getenv("MATCHING_WORKER_SIGNING_KEY", "")
    if not signing_key:
        return ""
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": worker_id,
            "tenant_id": "matching-platform",
            "roles": ["matching.worker"],
            "jti": str(uuid4()),
            "iat": now,
            "exp": now + timedelta(hours=24),
            "iss": os.getenv("MATCHING_AUTH_ISSUER", "https://matching.local/issuer"),
            "aud": os.getenv("MATCHING_AUTH_AUDIENCE", "matching-api"),
        },
        signing_key,
        algorithm="HS256",
    )


def main() -> int:
    logging.basicConfig(level=os.getenv("MATCHING_WORKER_LOG_LEVEL", "INFO"))
    worker = build_worker()
    monitor = WorkerMonitoringServer(
        worker.metrics_registry,
        worker,
        host=os.getenv("MATCHING_WORKER_METRICS_HOST", "0.0.0.0"),
        port=int(os.getenv("MATCHING_WORKER_METRICS_PORT", "9091")),
    )
    monitor.start()

    def request_stop(signum: int, frame: object) -> None:
        timeout = float(os.getenv("MATCHING_WORKER_SHUTDOWN_TIMEOUT_SECONDS", "30"))
        worker.shutdown(timeout)

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        worker.run_forever()
    except KeyboardInterrupt:
        timeout = float(os.getenv("MATCHING_WORKER_SHUTDOWN_TIMEOUT_SECONDS", "30"))
        worker.shutdown(timeout)
    finally:
        timeout = float(os.getenv("MATCHING_WORKER_SHUTDOWN_TIMEOUT_SECONDS", "30"))
        worker.shutdown(timeout)
        monitor.stop()
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a process entry point
    raise SystemExit(main())
