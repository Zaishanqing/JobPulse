from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from app.api.schemas import CreateAnalysisRunRequest
from app.application.worker import AnalysisWorker
from app.infrastructure.repository import SqlAlchemyAnalysisRunRepository
from app.acquisition.infrastructure.acquisition_store import SqlAlchemyAcquisitionStore


def command(payload, *, request_id="request-001", key="idem-001"):
    value = dict(payload, request_id=request_id, idempotency_key=key)
    return CreateAnalysisRunRequest.model_validate(value).to_command()


def test_two_workers_cannot_claim_the_same_run(database, payload):
    repository = SqlAlchemyAnalysisRunRepository(database.sessions)
    run = repository.create_or_get(command(payload), max_attempts=3)
    now = datetime.now(timezone.utc)

    def claim(worker_id):
        claimed = repository.claim(worker_id, now=now, lease=timedelta(seconds=30))
        return claimed.id if claimed else None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ["worker-a", "worker-b"]))
    assert results.count(run.id) == 1
    assert results.count(None) == 1


def test_two_acquisition_workers_cannot_claim_the_same_job(database):
    store = SqlAlchemyAcquisitionStore(database.sessions)
    source = store.create_source({
        "name": "fixed-policy-snapshot",
        "source_type": "policy",
        "endpoint_config": {"snapshot": "policy-2026-01"},
    })
    job = store.create_crawl_job({
        "source_id": source["id"],
        "window_start": "2026-01-01T00:00:00Z",
        "window_end": "2026-02-01T00:00:00Z",
    })
    now = datetime.now(timezone.utc)

    def claim(worker_id):
        claimed = store.claim_crawl_job(
            worker_id, now=now, lease=timedelta(seconds=30)
        )
        return claimed["id"] if claimed else None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ["acquisition-a", "acquisition-b"]))
    assert results.count(job["id"]) == 1
    assert results.count(None) == 1


def test_worker_status_flow_and_failure_retry(database, payload):
    repository = SqlAlchemyAnalysisRunRepository(database.sessions)
    run = repository.create_or_get(command(payload), max_attempts=2)
    attempts = []

    def flaky(_run):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("temporary failure")
        return {"snapshots": 1, "signals": 1, "predictions": 1, "skill_trends": 0}

    worker = AnalysisWorker(
        repository,
        worker_id="worker-a",
        lease_seconds=30,
        retry_delay_seconds=0,
        executor=flaky,
    )
    assert worker.run_once()
    assert repository.get(run.id).status.value == "pending"
    assert worker.run_once()
    assert repository.get(run.id).status.value == "succeeded"
    assert repository.get(run.id).attempt_count == 2


def test_worker_success_log_contains_actual_result_counts(database, payload):
    repository = SqlAlchemyAnalysisRunRepository(database.sessions)
    run = repository.create_or_get(command(payload), max_attempts=2)
    worker = AnalysisWorker(
        repository,
        worker_id="worker-a",
        lease_seconds=30,
        retry_delay_seconds=0,
        executor=lambda _run: {
            "snapshots": 4,
            "signals": 3,
            "predictions": 2,
            "skill_trends": 0,
        },
    )
    assert worker.run_once()
    succeeded = repository.logs(run.id)[-1]
    assert succeeded.message == "analysis run succeeded"
    assert succeeded.details == {
        "snapshots": 4,
        "signals": 3,
        "predictions": 2,
        "skill_trends": 0,
    }


def test_worker_does_not_mark_empty_output_as_success(database, payload):
    repository = SqlAlchemyAnalysisRunRepository(database.sessions)
    run = repository.create_or_get(command(payload), max_attempts=2)
    worker = AnalysisWorker(
        repository,
        worker_id="worker-a",
        lease_seconds=30,
        retry_delay_seconds=0,
        executor=lambda _run: None,
    )
    assert worker.run_once()
    current = repository.get(run.id)
    assert current.status.value == "pending"
    assert repository.logs(run.id)[-1].message == "analysis produced no predictions"


def test_two_outbox_processors_cannot_claim_the_same_event(database):
    store = SqlAlchemyAcquisitionStore(database.sessions)
    entry = store.enqueue_outbox("Bundle", "bundle-1", "bundle_ready", {})
    now = datetime.now(timezone.utc)

    def claim(worker_id):
        values = store.claim_outbox(
            worker_id, now=now, lease=timedelta(seconds=30), limit=1
        )
        return values[0]["id"] if values else None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ["outbox-a", "outbox-b"]))
    assert results.count(entry["id"]) == 1
    assert results.count(None) == 1


def test_expired_lease_is_recovered(database, payload):
    repository = SqlAlchemyAnalysisRunRepository(database.sessions)
    run = repository.create_or_get(command(payload), max_attempts=3)
    claimed_at = datetime.now(timezone.utc) + timedelta(seconds=1)
    repository.claim("dead-worker", now=claimed_at, lease=timedelta(seconds=1))
    assert repository.recover_expired(now=claimed_at + timedelta(minutes=5)) == 1
    recovered = repository.get(run.id)
    assert recovered.status.value == "pending"
    assert "lease_expired" in [item.event for item in repository.logs(run.id)]


def test_cancel_running_run_is_observed_on_completion(database, payload):
    repository = SqlAlchemyAnalysisRunRepository(database.sessions)
    run = repository.create_or_get(command(payload), max_attempts=3)
    repository.claim("worker-a", now=datetime.now(timezone.utc), lease=timedelta(seconds=30))
    assert repository.cancel(run.id).cancel_requested is True
    assert repository.succeed(run.id, "worker-a")
    assert repository.get(run.id).status.value == "cancelled"
