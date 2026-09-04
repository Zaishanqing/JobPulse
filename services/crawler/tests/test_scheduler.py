from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

from patches import scheduler


@pytest.fixture(autouse=True)
def reset_scheduler_state(monkeypatch):
    monkeypatch.setattr(scheduler, "_scheduler", None)
    monkeypatch.setattr(scheduler, "_job_ids", [])


def test_load_schedules_handles_missing_empty_and_enabled_config(tmp_path):
    assert scheduler._load_schedules(tmp_path / "missing.yaml") == []
    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    assert scheduler._load_schedules(empty) == []
    config = tmp_path / "schedule.yaml"
    config.write_text(
        yaml.safe_dump({"schedules": [{"task_type": "boss", "enabled": True}]}),
        encoding="utf-8",
    )
    assert scheduler._load_schedules(config)[0]["task_type"] == "boss"


def test_create_job_func_rejects_unknown_and_runs_success_and_failure(monkeypatch):
    with pytest.raises(ValueError, match="unsupported|不支持"):
        scheduler._create_job_func({"task_type": "unknown", "name": "bad"})

    calls = []
    monkeypatch.setitem(
        scheduler._EXECUTORS,
        "boss",
        lambda **kwargs: calls.append(kwargs) or 3,
    )
    job = scheduler._create_job_func(
        {"task_type": "boss", "name": "daily", "user_id": 7, "params": {"x": 1}}
    )
    job()
    assert calls[0]["user_id"] == 7
    assert calls[0]["params"] == {"x": 1}
    assert job.__name__ == "scheduled_boss_daily"

    monkeypatch.setitem(
        scheduler._EXECUTORS,
        "boss",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("crawl failed")),
    )
    scheduler._create_job_func({"task_type": "boss", "name": "failure"})()


class FakeScheduler:
    def __init__(self, daemon=True):
        self._daemon = daemon
        self.running = False
        self.jobs = []
        self.shutdown_calls = []

    def add_job(self, func, *, trigger, id, name, replace_existing):
        job = SimpleNamespace(
            id=id,
            name=name,
            trigger=trigger,
            next_run_time=SimpleNamespace(isoformat=lambda: "2026-09-01T08:00:00"),
        )
        self.jobs.append(job)
        return job

    def start(self):
        self.running = True

    def shutdown(self, wait=False):
        self.shutdown_calls.append(wait)
        self.running = False

    def get_jobs(self):
        return self.jobs


def test_start_scheduler_filters_disabled_and_invalid_cron(monkeypatch, tmp_path):
    config = tmp_path / "schedule.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "schedules": [
                    {
                        "name": "disabled",
                        "enabled": False,
                        "task_type": "boss",
                        "cron": "0 8 * * *",
                    },
                    {
                        "name": "invalid",
                        "enabled": True,
                        "task_type": "boss",
                        "cron": "bad",
                    },
                    {
                        "name": "active",
                        "enabled": True,
                        "task_type": "liepin",
                        "cron": "6 8 * * *",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(scheduler, "_HAS_APSCHEDULER", True)
    monkeypatch.setattr(scheduler, "BackgroundScheduler", FakeScheduler)
    monkeypatch.setattr(scheduler, "CronTrigger", lambda **kwargs: kwargs)

    result = scheduler.start_scheduler(config)

    assert result.running is True
    assert scheduler._job_ids == ["active"]
    assert scheduler.list_jobs()[0]["next_run_time"] == "2026-09-01T08:00:00"
    assert scheduler.get_status()["job_count"] == 1
    scheduler.stop_scheduler()
    assert scheduler._scheduler is None
    assert result.shutdown_calls == [False]


def test_start_scheduler_handles_missing_dependency_and_no_enabled(monkeypatch, tmp_path):
    monkeypatch.setattr(scheduler, "_HAS_APSCHEDULER", False)
    with pytest.raises(ImportError, match="APScheduler"):
        scheduler.start_scheduler(tmp_path / "missing.yaml")

    monkeypatch.setattr(scheduler, "_HAS_APSCHEDULER", True)
    config = tmp_path / "disabled.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "schedules": [
                    {"enabled": False, "task_type": "boss", "cron": "0 8 * * *"}
                ]
            }
        ),
        encoding="utf-8",
    )
    assert scheduler.start_scheduler(config) is None
    assert scheduler.list_jobs() == []
    assert scheduler.get_status()["running"] is False
    scheduler.stop_scheduler()
