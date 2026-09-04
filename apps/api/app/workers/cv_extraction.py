from __future__ import annotations

import logging
import signal
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event
from uuid import uuid4

from app.contexts.cv_ingestion import CVIngestionUseCases


LOGGER = logging.getLogger(__name__)


class CVExtractionWorker:
    """Lease-based CV worker with database-backed recovery."""

    def __init__(
        self,
        use_cases: CVIngestionUseCases,
        *,
        poll_interval_seconds: float,
        concurrency: int,
        lease_timeout_seconds: float,
        stale_recovery_interval_seconds: float,
        worker_id: str | None = None,
    ) -> None:
        self._use_cases = use_cases
        self._poll_interval = poll_interval_seconds
        self._concurrency = concurrency
        self._lease_timeout = lease_timeout_seconds
        self._recovery_interval = stale_recovery_interval_seconds
        self._worker_id = worker_id or f"cv-extraction-worker-{uuid4()}"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._executor: ThreadPoolExecutor | None = self._new_executor()
        self._active: dict[Future, str] = {}
        self._lock = threading.Lock()
        self._trigger_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._last_recovery = 0.0

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._lifecycle_lock:
            if self.is_running:
                return
            self._stop.clear()
            if self._executor is None:
                self._executor = self._new_executor()
            self._thread = threading.Thread(
                target=self._loop,
                name=f"{self._worker_id}-poller",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._stop.set()
            thread = self._thread
            if thread is not None:
                thread.join(timeout=max(5.0, self._poll_interval * 2))
            with self._trigger_lock:
                executor = self._executor
                self._executor = None
                if executor is not None:
                    executor.shutdown(wait=True, cancel_futures=False)
                self._reap()
            self._thread = None

    def trigger(self, limit: int) -> int:
        # Capacity, database claim, submit, and active registration form one
        # serialized scheduling decision. The poller and manual trigger cannot
        # both consume the same capacity window.
        with self._trigger_lock:
            self._reap()
            with self._lock:
                capacity = max(0, self._concurrency - len(self._active))
            executor = self._executor
            if executor is None:
                raise RuntimeError("CV extraction worker is stopped")
            claimed = 0
            for _ in range(min(limit, capacity)):
                task = self._use_cases.claim_next(
                    self._worker_id, self._lease_timeout
                )
                if task is None:
                    break
                future = executor.submit(
                    self._use_cases.execute_claimed,
                    task.task_id,
                    worker_id=self._worker_id,
                )
                with self._lock:
                    self._active[future] = task.task_id
                claimed += 1
            return claimed

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                now = time.monotonic()
                if now - self._last_recovery >= self._recovery_interval:
                    self._use_cases.recover_stale()
                    self._last_recovery = now
                self._heartbeat_active()
                self.trigger(self._concurrency)
            except Exception:
                LOGGER.exception("cv_extraction_worker_poll_failed")
            self._stop.wait(self._poll_interval)
        self._heartbeat_active()

    def _heartbeat_active(self) -> None:
        self._reap()
        with self._lock:
            task_ids = tuple(self._active.values())
        for task_id in task_ids:
            try:
                self._use_cases.heartbeat(
                    task_id, self._worker_id, self._lease_timeout
                )
            except Exception:
                LOGGER.exception(
                    "cv_extraction_worker_heartbeat_failed",
                    extra={"task_id": task_id},
                )

    def _reap(self) -> None:
        with self._lock:
            completed = [future for future in self._active if future.done()]
            for future in completed:
                task_id = self._active.pop(future)
                exception = future.exception()
                if exception is not None:
                    LOGGER.error(
                        "cv_extraction_task_failed",
                        exc_info=(type(exception), exception, exception.__traceback__),
                        extra={"task_id": task_id},
                    )

    def _new_executor(self) -> ThreadPoolExecutor:
        return ThreadPoolExecutor(
            max_workers=self._concurrency,
            thread_name_prefix="cv-extraction-task",
        )


def run_worker(runtime, *, stop: Event | None = None) -> None:
    """Keep the standalone CV worker alive until SIGINT/SIGTERM."""

    stopped = stop or Event()

    def request_stop(signum: int, _frame: object) -> None:
        LOGGER.info("cv_extraction_worker_stopping", extra={"signal": signum})
        stopped.set()

    handlers = {signal.SIGINT: signal.getsignal(signal.SIGINT)}
    sigterm = getattr(signal, "SIGTERM", None)
    if sigterm is not None:
        handlers[sigterm] = signal.getsignal(sigterm)
    try:
        signal.signal(signal.SIGINT, request_stop)
        if sigterm is not None:
            signal.signal(sigterm, request_stop)
        LOGGER.info("cv_extraction_worker_started")
        while not stopped.wait(1):
            if not runtime.container.cv_extraction_worker.is_running:
                raise RuntimeError("CV extraction worker stopped unexpectedly")
    finally:
        for signum, previous in handlers.items():
            signal.signal(signum, previous)


def main() -> None:
    """Start the CV worker with the production composition root."""

    from app.bootstrap.container import _build_runtime
    from app.core.config import settings
    from app.core.logging import configure_logging

    if not settings.CV_EXTRACTION_WORKER_ENABLED:
        raise RuntimeError("CV_EXTRACTION_WORKER_ENABLED must be true")
    if settings.JD_EXTRACTION_WORKER_ENABLED:
        raise RuntimeError("The standalone CV worker must not run the JD worker")
    configure_logging(settings.LOG_LEVEL)
    runtime = _build_runtime(settings)
    try:
        run_worker(runtime)
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
