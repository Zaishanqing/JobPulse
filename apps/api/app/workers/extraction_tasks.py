from __future__ import annotations

import threading
import time
import logging
import signal
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event
from uuid import uuid4

from app.contexts.extraction_tasks import ExtractionTaskUseCases


LOGGER = logging.getLogger(__name__)


class ExtractionTaskWorker:
    """Database-backed lightweight worker; the database remains the queue truth."""

    def __init__(
        self,
        use_cases: ExtractionTaskUseCases,
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
        self._worker_id = worker_id or f"extraction-worker-{uuid4()}"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._executor = ThreadPoolExecutor(
            max_workers=concurrency,
            thread_name_prefix="extraction-task",
        )
        self._active: dict[Future, str] = {}
        self._lock = threading.Lock()
        self._last_recovery = 0.0

    @property
    def worker_id(self) -> str:
        return self._worker_id

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name=f"{self._worker_id}-poller",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(5.0, self._poll_interval * 2))
        while True:
            self._heartbeat_active()
            with self._lock:
                if not self._active:
                    break
            time.sleep(min(self._poll_interval, self._lease_timeout / 3))
        self._executor.shutdown(wait=True, cancel_futures=False)

    def trigger(self, limit: int) -> int:
        self._reap()
        with self._lock:
            capacity = max(0, self._concurrency - len(self._active))
        claimed = 0
        for _ in range(min(limit, capacity)):
            task = self._use_cases.claim_next_extraction_task(
                self._worker_id, self._lease_timeout
            )
            if task is None:
                break
            future = self._executor.submit(self._execute, task.id)
            with self._lock:
                self._active[future] = task.id
            claimed += 1
        return claimed

    def wait_until_idle(self, timeout_seconds: float = 10) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            self._reap()
            with self._lock:
                if not self._active:
                    return True
            time.sleep(0.01)
        return False

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                now = time.monotonic()
                if now - self._last_recovery >= self._recovery_interval:
                    self._use_cases.recover_stale_extraction_tasks()
                    self._last_recovery = now
                self._heartbeat_active()
                self.trigger(self._concurrency)
            except Exception:
                # A broken task or transient database error must not stop the poller.
                pass
            self._stop.wait(self._poll_interval)
        self._heartbeat_active()

    def _execute(self, task_id: str) -> None:
        try:
            self._use_cases.run_extraction_task(task_id, claimed_by=self._worker_id)
        except Exception:
            # Persisted task state/lease is authoritative; stale recovery handles crashes.
            pass

    def _heartbeat_active(self) -> None:
        self._reap()
        with self._lock:
            task_ids = tuple(self._active.values())
        for task_id in task_ids:
            try:
                self._use_cases.heartbeat_extraction_task(
                    task_id, self._worker_id, self._lease_timeout
                )
            except Exception:
                continue

    def _reap(self) -> None:
        with self._lock:
            completed = [future for future in self._active if future.done()]
            for future in completed:
                self._active.pop(future, None)


def run_worker(runtime, *, stop: Event | None = None) -> None:
    """Keep the existing database-backed worker alive until SIGINT/SIGTERM."""

    stopped = stop or Event()

    def request_stop(signum: int, _frame: object) -> None:
        LOGGER.info("extraction_worker_stopping", extra={"signal": signum})
        stopped.set()

    handlers = {signal.SIGINT: signal.getsignal(signal.SIGINT)}
    sigterm = getattr(signal, "SIGTERM", None)
    if sigterm is not None:
        handlers[sigterm] = signal.getsignal(sigterm)
    try:
        signal.signal(signal.SIGINT, request_stop)
        if sigterm is not None:
            signal.signal(sigterm, request_stop)
        LOGGER.info("extraction_worker_started")
        while not stopped.wait(1):
            if not runtime.container.extraction_worker.is_running:
                raise RuntimeError("Extraction worker stopped unexpectedly")
    finally:
        for signum, previous in handlers.items():
            signal.signal(signum, previous)


def main() -> None:
    """Start the standalone worker process using the production composition root."""

    from app.bootstrap.container import _build_runtime
    from app.core.config import settings
    from app.core.logging import configure_logging

    if not settings.JD_EXTRACTION_WORKER_ENABLED:
        raise RuntimeError("JD_EXTRACTION_WORKER_ENABLED must be true")
    if not settings.JD_EXTRACTION_BASE_URL or not settings.JD_EXTRACTION_INTERNAL_TOKEN:
        raise RuntimeError("JD extraction integration must be configured")
    configure_logging(settings.LOG_LEVEL)
    runtime = _build_runtime(settings)
    try:
        run_worker(runtime)
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
