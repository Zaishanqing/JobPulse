from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from app.workers.cv_extraction import CVExtractionWorker


class _FakeCVUseCases:
    def __init__(self, task_ids: list[str], *, release: threading.Event | None = None):
        self._task_ids = task_ids
        self._lock = threading.Lock()
        self._release = release
        self.executed: list[str] = []
        self.executed_event = threading.Event()

    def claim_next(self, worker_id: str, lease_seconds: float):
        # The delay widens the old capacity race while the queue lock keeps each
        # fake database claim unique.
        time.sleep(0.01)
        with self._lock:
            if not self._task_ids:
                return None
            return SimpleNamespace(task_id=self._task_ids.pop(0))

    def execute_claimed(self, task_id: str, *, worker_id: str):
        with self._lock:
            self.executed.append(task_id)
            self.executed_event.set()
        if self._release is not None:
            self._release.wait(timeout=5)

    def recover_stale(self) -> int:
        return 0

    def heartbeat(
        self, task_id: str, worker_id: str, lease_seconds: float
    ) -> None:
        return None


def _worker(use_cases: _FakeCVUseCases, concurrency: int = 2) -> CVExtractionWorker:
    return CVExtractionWorker(
        use_cases,
        poll_interval_seconds=0.01,
        concurrency=concurrency,
        lease_timeout_seconds=1,
        stale_recovery_interval_seconds=10,
        worker_id="test-cv-worker",
    )


def test_cv_worker_processes_tasks_after_stop_and_restart():
    use_cases = _FakeCVUseCases(["task-1"])
    worker = _worker(use_cases, concurrency=1)
    try:
        worker.start()
        assert use_cases.executed_event.wait(timeout=2)
        worker.stop()

        use_cases.executed_event.clear()
        with use_cases._lock:
            use_cases._task_ids.append("task-2")
        worker.start()
        assert use_cases.executed_event.wait(timeout=2)
    finally:
        worker.stop()

    assert use_cases.executed == ["task-1", "task-2"]


def test_concurrent_triggers_never_exceed_configured_capacity():
    release = threading.Event()
    use_cases = _FakeCVUseCases(
        ["task-1", "task-2", "task-3", "task-4"],
        release=release,
    )
    worker = _worker(use_cases, concurrency=2)
    barrier = threading.Barrier(8)
    claimed: list[int] = []

    def trigger() -> None:
        barrier.wait(timeout=5)
        claimed.append(worker.trigger(2))

    threads = [threading.Thread(target=trigger) for _ in range(8)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            assert not thread.is_alive()
        with worker._lock:
            active_count = len(worker._active)
            active_task_ids = tuple(worker._active.values())
        assert sum(claimed) == 2
        assert active_count <= 2
        assert len(active_task_ids) == len(set(active_task_ids))
    finally:
        release.set()
        worker.stop()
