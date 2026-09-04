"""Deterministic FixedSnapshotConnector regression tests, separate from production paths."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from app.acquisition.infrastructure.acquisition_store import SqlAlchemyAcquisitionStore
from app.acquisition.application.crawl_service import CrawlService
from app.acquisition.infrastructure.connectors import ConnectorRegistry, FixedSnapshotConnector


UTC = timezone.utc


def test_create_and_get_source(database):
    store = SqlAlchemyAcquisitionStore(database.sessions)
    created = store.create_source({
        "name": "Test Job Board",
        "source_type": "job_board",
        "endpoint_config": {"url": "https://example.com/api"},
        "auth_config": {},
        "rate_limit_rps": 2.0,
        "compliance_policy": {},
    })
    assert created["name"] == "Test Job Board"
    assert created["status"] == "active"
    fetched = store.get_source(str(created["id"]))
    assert fetched is not None
    assert fetched["name"] == "Test Job Board"


def test_create_and_get_crawl_job(database):
    store = SqlAlchemyAcquisitionStore(database.sessions)
    source = store.create_source({
        "name": "Test Source", "source_type": "test",
        "endpoint_config": {}, "auth_config": {},
        "rate_limit_rps": 1.0, "compliance_policy": {},
    })
    job = store.create_crawl_job({
        "source_id": str(source["id"]),
        "window_start": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        "window_end": datetime(2026, 1, 7, tzinfo=UTC).isoformat(),
        "max_retries": 3,
    })
    assert job["status"] == "pending"
    assert job["source_id"] == source["id"]
    fetched = store.get_crawl_job(str(job["id"]))
    assert fetched is not None
    assert fetched["status"] == "pending"


def test_crawl_job_status_flow(database):
    store = SqlAlchemyAcquisitionStore(database.sessions)
    source = store.create_source({
        "name": "Test Source 2", "source_type": "test",
        "endpoint_config": {}, "auth_config": {},
        "rate_limit_rps": 1.0, "compliance_policy": {},
    })
    job = store.create_crawl_job({
        "source_id": str(source["id"]),
        "window_start": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        "window_end": datetime(2026, 1, 7, tzinfo=UTC).isoformat(),
        "max_retries": 3,
    })
    assert store.mark_job_running(str(job["id"]))
    assert store.get_crawl_job(str(job["id"]))["status"] == "running"
    assert store.mark_job_failed(str(job["id"]), "terminal test", retryable=False)
    assert store.get_crawl_job(str(job["id"]))["status"] == "failed"


def test_snapshot_dedup(database):
    store = SqlAlchemyAcquisitionStore(database.sessions)
    source = store.create_source({
        "name": "Test Source 3", "source_type": "test",
        "endpoint_config": {}, "auth_config": {},
        "rate_limit_rps": 1.0, "compliance_policy": {},
    })
    job = store.create_crawl_job({
        "source_id": str(source["id"]),
        "window_start": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        "window_end": datetime(2026, 1, 7, tzinfo=UTC).isoformat(),
        "max_retries": 3,
    })
    content = {"title": "Test", "description": "Test record"}
    snap1 = store.save_snapshot(
        str(job["id"]), str(source["id"]), "ext-1",
        content, "abc123", "json", {"source": "test"},
    )
    snap2 = store.save_snapshot(
        str(job["id"]), str(source["id"]), "ext-1",
        content, "abc123", "json", {"source": "test"},
    )
    assert snap1["id"] == snap2["id"]


def test_outbox_flow(database):
    store = SqlAlchemyAcquisitionStore(database.sessions)
    entry = store.enqueue_outbox("Bundle", "bundle-1", "bundle_ready", {"key": "value"})
    assert entry["status"] == "pending"
    pending = store.poll_outbox("pending", limit=10)
    assert any(e["id"] == entry["id"] for e in pending)
    claimed = store.claim_outbox(
        "outbox-test", now=datetime.now(UTC), lease=timedelta(seconds=30), limit=10
    )
    assert [item["id"] for item in claimed] == [entry["id"]]
    assert store.mark_outbox_processed(str(entry["id"]), "outbox-test")
    processed = store.poll_outbox("processed", limit=10)
    assert any(e["id"] == entry["id"] for e in processed)


def test_bundle_create_and_import(database):
    store = SqlAlchemyAcquisitionStore(database.sessions)
    source = store.create_source({
        "name": "Test Source 4", "source_type": "bundle_fixture",
        "endpoint_config": {"records": [{
            "external_id": "bundle-record",
            "raw_content": {"title": "Test JD"},
        }]}, "auth_config": {},
        "rate_limit_rps": 1.0, "compliance_policy": {},
    })
    job = store.create_crawl_job({
        "source_id": str(source["id"]),
        "window_start": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        "window_end": datetime(2026, 1, 7, tzinfo=UTC).isoformat(),
        "max_retries": 3,
    })
    result = CrawlService(
        store,
        registry=ConnectorRegistry({"bundle_fixture": FixedSnapshotConnector()}),
    ).execute_job(str(job["id"]))
    assert result["status"] == "succeeded"
    bundle = store.get_bundle_for_job(str(job["id"]))
    assert bundle is not None
    assert bundle["job_id"] == job["id"]
    assert bundle["source_id"] == source["id"]
    assert bundle["record_count"] == 1
    assert bundle["payload"]["records"][0]["source_version"] == "1"
    assert bundle["snapshot_ids"] == [
        store.list_snapshot_observations(str(job["id"]))[0]["snapshot_id"]
    ]
    assert bundle["window_start"] == datetime(2026, 1, 1, tzinfo=UTC)
    assert bundle["window_end"] == datetime(2026, 1, 7, tzinfo=UTC)
    assert bundle["payload"]["job_id"] == job["id"]
    assert bundle["payload"]["source_id"] == source["id"]
    assert bundle["payload"]["snapshot_ids"] == bundle["snapshot_ids"]
    assert bundle["payload"]["acquisition_window"] == {
        "start": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        "end": datetime(2026, 1, 7, tzinfo=UTC).isoformat(),
    }
    ready = [
        item for item in store.poll_outbox("pending", limit=10)
        if item["event_type"] == "bundle_ready"
    ]
    assert len(ready) == 1
    assert ready[0]["aggregate_id"] == bundle["id"]
    assert ready[0]["payload"]["job_id"] == job["id"]
    assert store.create_bundle_for_job(
        str(job["id"]), str(source["id"]), bundle["snapshot_ids"], "raw_snapshot",
    )["id"] == bundle["id"]
    assert bundle["status"] == "ready"


def test_crawl_service_records_new_and_duplicate_result_counts(database):
    store = SqlAlchemyAcquisitionStore(database.sessions)
    record = {
        "external_id": "same-record",
        "raw_content": {"title": "Stable record", "content": "same content"},
        "metadata": {"fixture": True},
    }
    source = store.create_source({
        "name": "Explicit fixture",
        "source_type": "test_fixture",
        "endpoint_config": {"records": [record]},
        "auth_config": {},
        "rate_limit_rps": 100.0,
        "compliance_policy": {"mode": "fixed_snapshot"},
    })
    service = CrawlService(
        store,
        registry=ConnectorRegistry({"test_fixture": FixedSnapshotConnector()}),
    )

    results = []
    for _ in range(2):
        job = store.create_crawl_job({
            "source_id": str(source["id"]),
            "window_start": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
            "window_end": datetime(2026, 1, 7, tzinfo=UTC).isoformat(),
            "max_retries": 0,
        })
        results.append(service.execute_job(str(job["id"])))

    assert results[0]["status"] == "succeeded"
    assert (results[0]["fetched_count"], results[0]["new_snapshot_count"], results[0]["duplicate_count"]) == (1, 1, 0)
    assert results[1]["status"] == "succeeded"
    assert (results[1]["fetched_count"], results[1]["new_snapshot_count"], results[1]["duplicate_count"]) == (1, 0, 1)
    first_bundle = store.get_bundle_for_job(str(results[0]["id"]))
    second_bundle = store.get_bundle_for_job(str(results[1]["id"]))
    assert first_bundle is not None and second_bundle is not None
    assert first_bundle["snapshot_ids"] == second_bundle["snapshot_ids"]
    assert len(store.list_snapshots(str(source["id"]), limit=10)) == 1
    assert len(store.list_snapshot_observations(str(results[0]["id"]))) == 1
    assert len(store.list_snapshot_observations(str(results[1]["id"]))) == 1


def test_manual_bundle_rejects_missing_cross_source_and_unobserved_snapshots(database):
    store = SqlAlchemyAcquisitionStore(database.sessions)

    def collect(source_name: str, external_id: str, source=None):
        if source is None:
            source_type = f"fixture_{source_name}"
            source = store.create_source({
                "name": source_name,
                "source_type": source_type,
                "endpoint_config": {"records": [{
                    "external_id": external_id,
                    "raw_content": {"title": external_id},
                }]},
                "auth_config": {},
                "rate_limit_rps": 100.0,
                "compliance_policy": {"mode": "fixed_snapshot"},
            })
        else:
            source_type = str(source["source_type"])
            source = store.update_source(str(source["id"]), {
                "endpoint_config": {"records": [{
                    "external_id": external_id,
                    "raw_content": {"title": external_id},
                }]},
            })
        job = store.create_crawl_job({
            "source_id": str(source["id"]),
            "window_start": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
            "window_end": datetime(2026, 1, 7, tzinfo=UTC).isoformat(),
            "max_retries": 0,
        })
        result = CrawlService(
            store,
            registry=ConnectorRegistry({source_type: FixedSnapshotConnector()}),
        ).execute_job(str(job["id"]))
        assert result["status"] == "succeeded"
        snapshot_id = store.list_snapshot_observations(str(job["id"]))[0]["snapshot_id"]
        return source, job, snapshot_id

    source_a, job_a, snapshot_a = collect("source-a", "a-1")
    source_b, _job_b, snapshot_b = collect("source-b", "b-1")
    _source_a2, job_a2, snapshot_a2 = collect("source-a-second-job", "a-2", source_a)

    with pytest.raises(LookupError, match="snapshots not found"):
        store.create_bundle_for_job(
            str(job_a["id"]), str(source_a["id"]), ["missing-snapshot"], "manual",
        )
    with pytest.raises(ValueError, match="do not belong to source"):
        store.create_bundle_for_job(
            str(job_a["id"]), str(source_a["id"]), [snapshot_b], "manual",
        )
    with pytest.raises(ValueError, match="were not observed"):
        store.create_bundle_for_job(
            str(job_a["id"]), str(source_a["id"]), [snapshot_a2], "manual",
        )
    assert snapshot_a != snapshot_a2
    assert source_a["id"] != source_b["id"]


def test_bundle_failure_rolls_back_snapshots_observations_and_outbox(database, monkeypatch):
    store = SqlAlchemyAcquisitionStore(database.sessions)
    source = store.create_source({
        "name": "rollback-source",
        "source_type": "rollback_fixture",
        "endpoint_config": {"records": [{
            "external_id": "rollback-record",
            "raw_content": {"title": "must roll back"},
        }]},
        "auth_config": {},
        "rate_limit_rps": 100.0,
        "compliance_policy": {"mode": "fixed_snapshot"},
    })
    job = store.create_crawl_job({
        "source_id": str(source["id"]),
        "window_start": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        "window_end": datetime(2026, 1, 7, tzinfo=UTC).isoformat(),
        "max_retries": 0,
    })

    def fail_bundle(*args, **kwargs):
        raise RuntimeError("injected bundle failure")

    monkeypatch.setattr(store, "_create_bundle", fail_bundle)
    result = CrawlService(
        store,
        registry=ConnectorRegistry({"rollback_fixture": FixedSnapshotConnector()}),
    ).execute_job(str(job["id"]))

    assert result["status"] == "failed"
    assert "injected bundle failure" in str(result["error_message"])
    assert store.list_snapshots(str(source["id"]), limit=10) == []
    assert store.list_snapshot_observations(str(job["id"])) == []
    assert store.get_bundle_for_job(str(job["id"])) is None
    assert store.poll_outbox("pending", limit=10) == []


def test_concurrent_jobs_keep_snapshot_identity_and_independent_lineage(database):
    store = SqlAlchemyAcquisitionStore(database.sessions)
    source = store.create_source({
        "name": "concurrent-source",
        "source_type": "concurrent_fixture",
        "endpoint_config": {},
        "auth_config": {},
        "rate_limit_rps": 100.0,
        "compliance_policy": {},
    })
    jobs = [
        store.create_crawl_job({
            "source_id": str(source["id"]),
            "window_start": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
            "window_end": datetime(2026, 1, 7, tzinfo=UTC).isoformat(),
            "max_retries": 0,
        })
        for _ in range(2)
    ]
    for job in jobs:
        assert store.mark_job_running(str(job["id"]))
    records = [{
        "external_id": "concurrent-record",
        "raw_content": {"title": "same concurrent content"},
        "source_version": "concurrent.v1",
        "content_type": "json",
        "metadata": {},
    }]

    def complete(job_id: str):
        return store.complete_crawl_job(job_id, str(source["id"]), records)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(complete, [str(job["id"]) for job in jobs]))

    snapshot_ids = [result["bundle"]["snapshot_ids"][0] for result in results]
    assert snapshot_ids[0] == snapshot_ids[1]
    assert len(store.list_snapshots(str(source["id"]), limit=10)) == 1
    for job in jobs:
        assert store.get_crawl_job(str(job["id"]))["status"] == "succeeded"
        assert store.list_snapshot_observations(str(job["id"]))[0]["snapshot_id"] == snapshot_ids[0]
    counts = sorted(
        (
            store.get_crawl_job(str(job["id"]))["new_snapshot_count"],
            store.get_crawl_job(str(job["id"]))["duplicate_count"],
        )
        for job in jobs
    )
    assert counts == [(0, 1), (1, 0)]
