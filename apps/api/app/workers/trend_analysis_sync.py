from __future__ import annotations

import logging
import threading
from typing import Protocol

from app.contexts.market_intelligence import ManageTrendReports


LOGGER = logging.getLogger(__name__)


class TrendSyncLeadership(Protocol):
    def acquire(self) -> bool: ...
    def release(self) -> None: ...


class TrendAnalysisSynchronizer:
    """Continuously project terminal trend runs into the main task store."""

    def __init__(
        self,
        use_cases: ManageTrendReports,
        *,
        leadership: TrendSyncLeadership,
        poll_interval_seconds: float,
        batch_size: int = 50,
    ) -> None:
        self._use_cases = use_cases
        self._leadership = leadership
        self._poll_interval = poll_interval_seconds
        self._batch_size = batch_size
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="trend-analysis-synchronizer",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(5.0, self._poll_interval * 2))

    def synchronize_once(self) -> int:
        return self._use_cases.synchronize_active_tasks(self._batch_size)

    def _loop(self) -> None:
        owns_leadership = False
        try:
            while not self._stop.is_set() and not owns_leadership:
                owns_leadership = self._leadership.acquire()
                if not owns_leadership:
                    self._stop.wait(self._poll_interval)
            while not self._stop.is_set():
                try:
                    self.synchronize_once()
                except Exception as exc:
                    LOGGER.exception(
                        "trend_analysis_synchronization_failed",
                        extra={"error": str(exc)},
                    )
                self._stop.wait(self._poll_interval)
        finally:
            if owns_leadership:
                self._leadership.release()
