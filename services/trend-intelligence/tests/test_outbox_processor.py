from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.acquisition.application.outbox_processor import OutboxProcessor
from app.acquisition.ports.trend_input import TrendInputImportResult


class FakeTrendInput:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[str] = []
        self.imported: set[str] = set()

    def import_bundle(self, bundle_id: str) -> TrendInputImportResult:
        self.calls.append(bundle_id)
        if self.error:
            raise self.error
        duplicate = bundle_id in self.imported
        self.imported.add(bundle_id)
        return TrendInputImportResult(
            bundle_id=bundle_id,
            analysis_run_id="run-1",
            imported_count=0 if duplicate else 1,
            duplicate_count=1 if duplicate else 0,
            status="already_imported" if duplicate else "imported",
        )

    def records_for_run(self, run_id: str, source: str):
        return []


class FakeOutboxStore:
    def __init__(self, *, event_type: str = "bundle_ready", lose_lease: bool = False) -> None:
        self.entry = {
            "id": "outbox-1",
            "aggregate_type": "Bundle",
            "aggregate_id": "bundle-1",
            "event_type": event_type,
            "payload": {"bundle_id": "bundle-1"},
            "status": "pending",
            "retry_count": 0,
            "error_message": None,
            "lease_owner": None,
        }
        self.lose_lease = lose_lease

    def recover_expired_outbox(self, *, now):
        return 0

    def claim_outbox(self, worker_id, *, now, lease, limit):
        if self.entry["status"] != "pending":
            return []
        self.entry["status"] = "processing"
        self.entry["lease_owner"] = worker_id
        return [dict(self.entry)]

    def mark_outbox_processed(self, outbox_id, worker_id):
        if self.lose_lease:
            self.entry["lease_owner"] = "another-worker"
            return False
        if self.entry["lease_owner"] != worker_id:
            return False
        self.entry["status"] = "processed"
        self.entry["error_message"] = None
        return True

    def mark_outbox_failed(self, outbox_id, worker_id, error):
        if self.entry["lease_owner"] != worker_id:
            return False
        self.entry["retry_count"] += 1
        self.entry["status"] = "failed" if self.entry["retry_count"] >= 3 else "pending"
        self.entry["error_message"] = error
        self.entry["lease_owner"] = None
        return True


def processor(store: FakeOutboxStore, adapter: FakeTrendInput) -> OutboxProcessor:
    return OutboxProcessor(store, adapter, worker_id="outbox-worker")


def test_outbox_marks_processed_only_after_adapter_success():
    store = FakeOutboxStore()
    adapter = FakeTrendInput()
    assert processor(store, adapter).run_once(now=datetime.now(timezone.utc))
    assert adapter.calls == ["bundle-1"]
    assert store.entry["status"] == "processed"
    assert store.entry["error_message"] is None


def test_bundle_replay_is_idempotent():
    store = FakeOutboxStore()
    adapter = FakeTrendInput()
    worker = processor(store, adapter)
    assert worker.run_once()
    store.entry["status"] = "pending"
    store.entry["lease_owner"] = None
    assert worker.run_once()
    assert adapter.calls == ["bundle-1", "bundle-1"]
    assert adapter.imported == {"bundle-1"}
    assert store.entry["status"] == "processed"


def test_downstream_failure_keeps_event_retryable_with_error_message():
    store = FakeOutboxStore()
    adapter = FakeTrendInput(RuntimeError("trend database unavailable"))
    assert processor(store, adapter).run_once()
    assert store.entry["status"] == "pending"
    assert store.entry["retry_count"] == 1
    assert "trend database unavailable" in str(store.entry["error_message"])


def test_downstream_failure_exhausts_existing_retry_budget():
    store = FakeOutboxStore()
    adapter = FakeTrendInput(RuntimeError("trend database unavailable"))
    worker = processor(store, adapter)
    for _ in range(3):
        assert worker.run_once()
    assert store.entry["status"] == "failed"
    assert store.entry["retry_count"] == 3
    assert len(adapter.calls) == 3


def test_unknown_event_is_retried_then_exhausted():
    store = FakeOutboxStore(event_type="unknown_event")
    adapter = FakeTrendInput()
    worker = processor(store, adapter)
    for _ in range(3):
        assert worker.run_once()
    assert adapter.calls == []
    assert store.entry["status"] == "failed"
    assert store.entry["retry_count"] == 3
    assert "unsupported acquisition outbox event" in str(store.entry["error_message"])


def test_lost_lease_never_marks_event_processed(caplog):
    store = FakeOutboxStore(lose_lease=True)
    adapter = FakeTrendInput()
    with caplog.at_level(logging.ERROR):
        assert processor(store, adapter).run_once()
    assert adapter.calls == ["bundle-1"]
    assert store.entry["status"] == "processing"
    assert store.entry["lease_owner"] == "another-worker"
    assert "acquisition_outbox_entry_failed" in caplog.text
