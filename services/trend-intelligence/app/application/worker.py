from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timedelta, timezone
from threading import Event

from app.domain.analysis_run import AnalysisRun
from app.ports.repository import AnalysisRunRepository


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AnalysisWorker:
    def __init__(
        self,
        repository: AnalysisRunRepository,
        *,
        worker_id: str,
        lease_seconds: float,
        retry_delay_seconds: float,
        heartbeat_seconds: float | None = None,
        executor: Callable[[AnalysisRun], Mapping[str, int] | None] | None = None,
    ) -> None:
        self.repository = repository
        self.worker_id = worker_id
        self.lease = timedelta(seconds=lease_seconds)
        self.retry_delay = timedelta(seconds=retry_delay_seconds)
        self.heartbeat_seconds = heartbeat_seconds or max(lease_seconds / 3, 0.1)
        self.executor = executor or self._execute_skeleton

    @staticmethod
    def _execute_skeleton(_run: AnalysisRun) -> None:
        """Fallback executor used only when no market prediction pipeline is configured."""

    def run_once(self, *, now: datetime | None = None) -> bool:
        current = now or utc_now()
        self.repository.recover_expired(now=current)
        run = self.repository.claim(self.worker_id, now=current, lease=self.lease)
        if run is None:
            return False
        try:
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="trend-analysis-executor") as pool:
                future = pool.submit(self.executor, run)
                while True:
                    try:
                        result_summary = future.result(timeout=self.heartbeat_seconds)
                        break
                    except TimeoutError:
                        if not self.repository.renew_lease(
                            run.id,
                            self.worker_id,
                            until=utc_now() + self.lease,
                        ):
                            raise RuntimeError("worker lease was lost during execution")
        except Exception as exc:
            self.repository.fail(
                run.id,
                self.worker_id,
                str(exc),
                retry_at=utc_now() + self.retry_delay,
            )
            return True
        summary = dict(result_summary or {})
        expected_key = "skill_trends" if run.run_type == "position_skill_trend" else "predictions"
        if int(summary.get(expected_key, 0)) <= 0:
            self.repository.fail(
                run.id,
                self.worker_id,
                f"analysis produced no {expected_key}",
                retry_at=utc_now() + self.retry_delay,
            )
            return True
        self.repository.succeed(run.id, self.worker_id, summary)
        return True

    def run_forever(self, stop: Event, *, poll_seconds: float) -> None:
        while not stop.is_set():
            if not self.run_once():
                stop.wait(poll_seconds)
