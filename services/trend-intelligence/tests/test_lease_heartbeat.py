from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from threading import Thread

from app.api.schemas import CreateAnalysisRunRequest
from app.application.worker import AnalysisWorker
from app.infrastructure.repository import SqlAlchemyAnalysisRunRepository


def test_long_execution_renews_lease_and_prevents_stale_recovery(database, payload):
    repository = SqlAlchemyAnalysisRunRepository(database.sessions)
    run = repository.create_or_get(CreateAnalysisRunRequest.model_validate(payload).to_command(), max_attempts=3)
    def execute(_run):
        time.sleep(0.25)
        return {"snapshots": 1, "signals": 1, "predictions": 1, "skill_trends": 0}

    worker = AnalysisWorker(repository, worker_id="heartbeat-worker", lease_seconds=0.12, heartbeat_seconds=0.03, retry_delay_seconds=0, executor=execute)
    thread = Thread(target=worker.run_once)
    thread.start()
    time.sleep(0.16)
    assert repository.recover_expired(now=datetime.now(timezone.utc)) == 0
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert repository.get(run.id).status.value == "succeeded"


def test_crashed_worker_lease_remains_recoverable(database, payload):
    repository = SqlAlchemyAnalysisRunRepository(database.sessions)
    run = repository.create_or_get(CreateAnalysisRunRequest.model_validate(payload).to_command(), max_attempts=3)
    claimed_at = datetime.now(timezone.utc)
    repository.claim("crashed", now=claimed_at, lease=timedelta(milliseconds=10))
    assert repository.recover_expired(now=claimed_at + timedelta(seconds=1)) == 1
    assert repository.get(run.id).status.value == "pending"
