"""Independent C3 vector-index worker process."""

from __future__ import annotations

import logging
import os
import signal
import socket
import time

from app.application.vector_index_worker import VectorIndexWorker
from app.application.vector_indexing import VectorOutboxLifecycleService
from app.infrastructure.http_embedding_adapter import HttpEmbeddingAdapter
from app.infrastructure.http_sources import HttpCVProfileSource, HttpPositionProfileSource
from app.infrastructure.metrics import MetricsRegistry
from app.infrastructure.persistence_configuration import build_persistence
from app.infrastructure.qdrant_vector_store import QdrantVectorStoreAdapter
from app.infrastructure.structured_logging import StructuredLogger
from app.infrastructure.worker_monitoring import WorkerMonitoringServer


class VectorWorkerProcess:
    def __init__(self, worker: VectorIndexWorker, worker_id: str, metrics: MetricsRegistry):
        self._worker = worker
        self._worker_id = worker_id
        self._metrics = metrics
        self._stopping = False

    @property
    def is_stopping(self) -> bool:
        return self._stopping

    @property
    def readiness_status(self) -> str:
        return "stopping" if self._stopping else "ready"

    def stop(self) -> None:
        self._stopping = True

    def run_forever(self) -> None:
        idle = float(os.getenv("MATCHING_VECTOR_WORKER_IDLE_SECONDS", "0.25"))
        batch = int(os.getenv("MATCHING_VECTOR_WORKER_EVENT_BATCH_SIZE", "20"))
        while not self._stopping:
            results = self._worker.run_batch(self._worker_id, limit=batch)
            if not results:
                time.sleep(idle)


def build_process() -> tuple[VectorWorkerProcess, MetricsRegistry]:
    env = os.environ
    semantic_mode = env.get("MATCHING_SEMANTIC_MODE", "disabled").strip().lower()
    semantic_demo = env.get("MATCHING_SEMANTIC_DEMO", "false").strip().lower() == "true"
    if semantic_demo and semantic_mode == "enabled":
        raise ValueError("competition semantic demo supports shadow mode only")
    if semantic_mode not in {"disabled", "shadow"}:
        raise ValueError("MATCHING_SEMANTIC_MODE must be disabled or shadow")
    if semantic_demo and semantic_mode != "shadow":
        raise ValueError("competition semantic demo requires shadow mode")
    if semantic_demo and (
        env.get("MATCHING_DENSE_ENABLED", "true").strip().lower() != "true"
        or env.get("MATCHING_SPARSE_ENABLED", "false").strip().lower() == "true"
        or env.get("MATCHING_RERANKER_ENABLED", "false").strip().lower() == "true"
    ):
        raise ValueError(
            "competition semantic demo requires dense=true, sparse=false and reranker=false"
        )
    required = (
        "MATCHING_DATABASE_URL",
        "MATCHING_CV_SOURCE_URL",
        "MATCHING_POSITION_SOURCE_URL",
        "MATCHING_UPSTREAM_SERVICE_TOKEN",
        "MATCHING_VECTOR_EMBEDDING_MODEL",
        "MATCHING_VECTOR_EMBEDDING_REVISION",
        "MATCHING_EMBEDDING_ENDPOINT",
        "MATCHING_QDRANT_URL",
    )
    missing = [name for name in required if not env.get(name, "").strip()]
    if missing:
        raise ValueError("vector worker configuration is incomplete: " + ", ".join(missing))
    persistence = build_persistence(env)
    model = env["MATCHING_VECTOR_EMBEDDING_MODEL"]
    revision = env["MATCHING_VECTOR_EMBEDDING_REVISION"]
    dimension = int(env.get("MATCHING_QDRANT_DIMENSION", "1024"))
    token = env["MATCHING_UPSTREAM_SERVICE_TOKEN"]
    source_options = {
        "service_token": token,
        "timeout_seconds": float(env.get("MATCHING_UPSTREAM_TIMEOUT_SECONDS", "5")),
        "max_retries": int(env.get("MATCHING_UPSTREAM_MAX_RETRIES", "2")),
        "retry_backoff_seconds": float(env.get("MATCHING_UPSTREAM_RETRY_BACKOFF_SECONDS", "0.1")),
    }
    cv_source = HttpCVProfileSource(
        env["MATCHING_CV_SOURCE_URL"], "/api/v1/contracts/cv-profiles", **source_options
    )
    position_source = HttpPositionProfileSource(
        env["MATCHING_POSITION_SOURCE_URL"],
        "/api/v1/contracts/position-profiles",
        **source_options,
    )
    embedding = HttpEmbeddingAdapter(
        env["MATCHING_EMBEDDING_ENDPOINT"],
        model=model,
        revision=revision,
        dimension=dimension,
        timeout_seconds=float(env.get("MATCHING_EMBEDDING_TIMEOUT_SECONDS", "10")),
    )
    vectors = QdrantVectorStoreAdapter(
        env["MATCHING_QDRANT_URL"],
        api_key=env.get("MATCHING_QDRANT_API_KEY") or None,
        collection_name=env.get("MATCHING_QDRANT_COLLECTION", "matching_fragments_v1"),
        index_revision=env.get(
            "MATCHING_VECTOR_INDEX_REVISION",
            env.get("MATCHING_QDRANT_COLLECTION", "matching_fragments_v1"),
        ),
        dimension=dimension,
        timeout_seconds=float(env.get("MATCHING_QDRANT_TIMEOUT_SECONDS", "5")),
        max_retries=int(env.get("MATCHING_QDRANT_MAX_RETRIES", "2")),
        retry_backoff_seconds=float(env.get("MATCHING_QDRANT_RETRY_BACKOFF_SECONDS", "0.1")),
    )
    if semantic_mode == "shadow":
        embedding.check_startup_contract()
        vectors.check_startup_contract()
    metrics = MetricsRegistry()
    lifecycle = VectorOutboxLifecycleService(
        persistence.unit_of_work,
        lease_seconds=float(env.get("MATCHING_VECTOR_OUTBOX_LEASE_SECONDS", "30")),
        retry_seconds=float(env.get("MATCHING_VECTOR_OUTBOX_RETRY_SECONDS", "2")),
    )
    worker = VectorIndexWorker(
        unit_of_work=persistence.unit_of_work,
        lifecycle=lifecycle,
        cv_source=cv_source,
        position_source=position_source,
        embedding=embedding,
        vectors=vectors,
        embedding_model=model,
        embedding_revision=revision,
        embedding_dimension=dimension,
        index_revision=env.get(
            "MATCHING_VECTOR_INDEX_REVISION",
            env.get("MATCHING_QDRANT_COLLECTION", "matching_fragments_v1"),
        ),
        batch_size=int(env.get("MATCHING_VECTOR_FRAGMENT_BATCH_SIZE", "32")),
        metrics=metrics,
        logger=StructuredLogger(),
    )
    worker_id = env.get("MATCHING_VECTOR_WORKER_ID") or f"{socket.gethostname()}-{os.getpid()}"
    return VectorWorkerProcess(worker, worker_id, metrics), metrics


def main() -> int:
    logging.basicConfig(level=os.getenv("MATCHING_WORKER_LOG_LEVEL", "INFO"))
    process, metrics = build_process()
    monitor = WorkerMonitoringServer(
        metrics,
        process,
        host=os.getenv("MATCHING_VECTOR_WORKER_METRICS_HOST", "0.0.0.0"),
        port=int(os.getenv("MATCHING_VECTOR_WORKER_METRICS_PORT", "9092")),
    )
    monitor.start()
    signal.signal(signal.SIGTERM, lambda *_: process.stop())
    signal.signal(signal.SIGINT, lambda *_: process.stop())
    try:
        process.run_forever()
    finally:
        process.stop()
        monitor.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
