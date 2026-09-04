from threading import Event

from app.workers.trend_analysis_sync import TrendAnalysisSynchronizer


class _UseCases:
    def __init__(self) -> None:
        self.called = Event()
        self.limits: list[int] = []

    def synchronize_active_tasks(self, limit: int) -> int:
        self.limits.append(limit)
        self.called.set()
        return 1


class _Leadership:
    def __init__(self) -> None:
        self.acquired = 0
        self.released = 0

    def acquire(self) -> bool:
        self.acquired += 1
        return True

    def release(self) -> None:
        self.released += 1


def test_synchronizer_runs_without_an_http_request_and_stops_cleanly():
    use_cases = _UseCases()
    leadership = _Leadership()
    worker = TrendAnalysisSynchronizer(
        use_cases,
        leadership=leadership,
        poll_interval_seconds=0.01,
        batch_size=7,
    )

    worker.start()
    assert use_cases.called.wait(1)
    worker.stop()

    assert use_cases.limits[0] == 7
    assert leadership.acquired == 1
    assert leadership.released == 1
    assert worker.is_running is False
