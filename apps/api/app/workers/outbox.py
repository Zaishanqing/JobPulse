"""CLI worker for dispatching durable integration events.

Run with ``python -m app.workers.outbox`` after database migrations are current.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event
from time import sleep

from sqlalchemy.engine import make_url

import app.models  # noqa: F401  # Register SQLAlchemy mappings before use.
from app.core.config import Settings, settings
from app.core.database import Database, create_database
from app.infrastructure.knowledge_graph import build_knowledge_graph_outbox_handlers
from app.infrastructure.outbox import SqlAlchemyOutboxDispatcher
from app.integrations.knowledge_graph.client import KnowledgeGraphClient
from app.infrastructure.matching_service import HttpMatchingServiceAdapter
from app.infrastructure.vector_index_outbox import MatchingVectorIndexOutboxHandler


LOGGER = logging.getLogger(__name__)


@dataclass
class OutboxWorkerRuntime:
    database: Database
    dispatcher: SqlAlchemyOutboxDispatcher
    client: KnowledgeGraphClient

    def close(self) -> None:
        self.client.close()
        self.database.dispose()


def build_worker_runtime(runtime_settings: Settings = settings) -> OutboxWorkerRuntime:
    """Build the same database and KG client resources used by production."""

    database = create_database(runtime_settings.DATABASE_URL)
    client = KnowledgeGraphClient(
        base_url=runtime_settings.KNOWLEDGE_GRAPH_BASE_URL,
        username=runtime_settings.KNOWLEDGE_GRAPH_SERVICE_USERNAME,
        password=runtime_settings.KNOWLEDGE_GRAPH_SERVICE_PASSWORD,
        timeout_seconds=runtime_settings.KNOWLEDGE_GRAPH_TIMEOUT_SECONDS,
    )
    handlers = build_knowledge_graph_outbox_handlers(
        database.session_factory, client, enabled=runtime_settings.KNOWLEDGE_GRAPH_ENABLED
    )
    event_types = {"jd.publication.created"}
    if runtime_settings.MATCHING_SERVICE_ENABLED:
        matching_client = HttpMatchingServiceAdapter(
            base_url=str(runtime_settings.MATCHING_SERVICE_BASE_URL),
            issuer=runtime_settings.MATCHING_SERVICE_ISSUER,
            audience=runtime_settings.MATCHING_SERVICE_AUDIENCE,
            signing_key=str(runtime_settings.MATCHING_SERVICE_SIGNING_KEY),
            timeout_seconds=runtime_settings.MATCHING_SERVICE_TIMEOUT_SECONDS,
            max_retries=runtime_settings.MATCHING_SERVICE_MAX_RETRIES,
            retry_backoff_seconds=runtime_settings.MATCHING_SERVICE_RETRY_BACKOFF_SECONDS,
        )
        vector_handler = MatchingVectorIndexOutboxHandler(matching_client)
        handlers[vector_handler.event_type] = vector_handler
        event_types.add(vector_handler.event_type)
    return OutboxWorkerRuntime(
        database,
        SqlAlchemyOutboxDispatcher(
            database.session_factory,
            handlers,
            event_types=event_types,
            lease_seconds=runtime_settings.KG_OUTBOX_LEASE_SECONDS,
            max_attempts=runtime_settings.KG_OUTBOX_MAX_ATTEMPTS,
        ),
        client,
    )


def run_worker(
    runtime: OutboxWorkerRuntime,
    *,
    stop: Event | None = None,
    worker_id: str | None = None,
    idle_sleep_seconds: float = settings.OUTBOX_IDLE_SLEEP_SECONDS,
    dispatch_once: bool = settings.OUTBOX_DISPATCH_ONCE,
    concurrency: int = 1,
) -> None:
    """Dispatch until SIGINT/SIGTERM asks this process to finish its loop."""

    stopped = stop or Event()
    identity = worker_id or f"outbox:{socket.gethostname()}:{os.getpid()}"
    if idle_sleep_seconds <= 0:
        raise ValueError("OUTBOX_IDLE_SLEEP_SECONDS must be greater than zero")
    if concurrency <= 0:
        raise ValueError("concurrency must be greater than zero")
    LOGGER.info(
        "outbox_worker_started",
        extra={
            "worker_id": identity,
            "database_target": make_url(str(runtime.database.engine.url)).render_as_string(
                hide_password=True
            ),
            "registered_event_types": sorted(runtime.dispatcher.handlers),
            "dispatch_once": dispatch_once,
            "concurrency": concurrency,
        },
    )

    def request_stop(signum: int, _frame: object) -> None:
        LOGGER.info("outbox_worker_stopping", extra={"signal": signum})
        stopped.set()

    handlers = {signal.SIGINT: signal.getsignal(signal.SIGINT)}
    try:
        sigterm = signal.SIGTERM
    except AttributeError:
        sigterm = None
    if sigterm is not None:
        handlers[sigterm] = signal.getsignal(sigterm)
    try:
        signal.signal(signal.SIGINT, request_stop)
        if sigterm is not None:
            signal.signal(sigterm, request_stop)
        with ThreadPoolExecutor(
            max_workers=concurrency,
            thread_name_prefix="kg-outbox",
        ) as executor:
            while not stopped.is_set():
                now = datetime.now(timezone.utc)
                futures = [
                    executor.submit(
                        runtime.dispatcher.dispatch_one,
                        identity if concurrency == 1 else f"{identity}:{slot}",
                        now,
                    )
                    for slot in range(concurrency)
                ]
                results = [future.result() for future in futures]
                if dispatch_once:
                    return
                if all(result is None for result in results) and not stopped.is_set():
                    sleep(idle_sleep_seconds)
    finally:
        for signum, previous in handlers.items():
            signal.signal(signum, previous)


def main() -> None:
    logging.basicConfig(level=settings.LOG_LEVEL)
    runtime = build_worker_runtime()
    try:
        run_worker(
            runtime,
            worker_id=settings.OUTBOX_WORKER_ID,
            idle_sleep_seconds=settings.KG_OUTBOX_POLL_INTERVAL_SECONDS,
            dispatch_once=settings.OUTBOX_DISPATCH_ONCE,
            concurrency=settings.KG_OUTBOX_WORKER_CONCURRENCY,
        )
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
