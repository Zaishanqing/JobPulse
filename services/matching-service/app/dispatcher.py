"""Independent outbox dispatcher entry point: ``python -m app.dispatcher``."""

from __future__ import annotations

import os
import signal
import socket
from threading import Event

from app.application.outbox_dispatcher import OutboxDispatcher
from app.infrastructure.persistence_configuration import build_persistence
from app.infrastructure.queue_configuration import build_task_queue


def build_dispatcher(dispatcher_id: str | None = None) -> OutboxDispatcher:
    persistence = build_persistence()
    queue = build_task_queue()
    if (
        os.getenv("MATCHING_RUNTIME_MODE", "development").strip().lower() == "production"
        and (persistence.provider != "postgres" or queue.provider != "redis")
    ):
        raise ValueError("production dispatcher requires PostgreSQL and Redis")
    selected_id = dispatcher_id or os.getenv("MATCHING_DISPATCHER_ID") or (
        f"{socket.gethostname()}-{os.getpid()}"
    )
    return OutboxDispatcher(
        persistence.unit_of_work,
        queue.queue,
        dispatcher_id=selected_id,
        lease_seconds=float(os.getenv("MATCHING_OUTBOX_LEASE_SECONDS", "30")),
        retry_interval_seconds=float(
            os.getenv("MATCHING_OUTBOX_RETRY_INTERVAL_SECONDS", "5")
        ),
    )


def main() -> int:
    stop = Event()

    def request_stop(signum: int, frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    build_dispatcher().run_forever(
        stop_event=stop,
        idle_sleep_seconds=float(os.getenv("MATCHING_OUTBOX_IDLE_SECONDS", "0.25")),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
