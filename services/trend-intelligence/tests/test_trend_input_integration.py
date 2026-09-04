from __future__ import annotations

from datetime import datetime, timezone

import httpx
from sqlalchemy import func, select

from app.acquisition.application.crawl_service import CrawlService
from app.acquisition.application.outbox_processor import OutboxProcessor
from app.acquisition.infrastructure.acquisition_models import AcquisitionBundleModel
from app.acquisition.infrastructure.acquisition_store import SqlAlchemyAcquisitionStore
from app.acquisition.infrastructure.connectors import ConnectorRegistry, PolicyConnector
from app.acquisition.infrastructure.trend_input import SqlAlchemyTrendInputAdapter
from app.application.market_prediction import MarketPrediction
from app.application.worker import AnalysisWorker
from app.domain.market import ExtractedTerm, week_start
from app.infrastructure.market_store import SqlAlchemyAnalysisDataStore
from app.infrastructure.models import AnalysisRunModel, SourceSnapshotModel, TrendInputRecordModel
from app.infrastructure.repository import SqlAlchemyAnalysisRunRepository


UTC = timezone.utc
CONNECTOR_CONFIG = {
    "domain_dictionary": {"人工智能": ["人工智能", "大模型"]},
    "policy_keywords": {"queries": ["人工智能"]},
}


class StaticExtractor:
    version = "bundle-integration-extractor.v1"

    def extract(self, snapshot):
        return [ExtractedTerm(
            snapshot_id=snapshot.id,
            term="large language model",
            score=0.9,
            week_start=week_start(snapshot.record.published_at),
            extractor_version=self.version,
        )]


def create_ready_bundle(store: SqlAlchemyAcquisitionStore):
    def policy_response(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"searchVO": {"catMap": {"policy": {"listVO": [{
            "url": "https://www.gov.cn/policy-bundle-1",
            "title": "人工智能产业发展政策",
            "summary": "推动人工智能大模型产业发展",
            "pubtimeStr": "2026-01-03",
            "puborg": "国务院",
        }]}}}})

    source = store.create_source({
        "name": "policy-bundle-source",
        "source_type": "policy",
        "endpoint_config": {"queries": ["人工智能"], "per_query": 20},
        "auth_config": {},
        "rate_limit_rps": 100.0,
        "compliance_policy": {"mode": "test_mock_http"},
    })
    job = store.create_crawl_job({
        "source_id": str(source["id"]),
        "window_start": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        "window_end": datetime(2026, 1, 7, tzinfo=UTC).isoformat(),
        "max_retries": 0,
    })
    with httpx.Client(transport=httpx.MockTransport(policy_response)) as client:
        result = CrawlService(
            store,
            registry=ConnectorRegistry({"policy": PolicyConnector(client, CONNECTOR_CONFIG)}),
        ).execute_job(str(job["id"]))
    assert result["status"] == "succeeded"
    bundle = store.get_bundle_for_job(str(job["id"]))
    assert bundle is not None and bundle["status"] == "ready"
    return bundle


def test_bundle_is_imported_once_and_used_by_real_analysis_run(database, credibility_store):
    acquisition_store = SqlAlchemyAcquisitionStore(database.sessions)
    bundle = create_ready_bundle(acquisition_store)
    adapter = SqlAlchemyTrendInputAdapter(database.sessions, max_attempts=3)
    outbox = OutboxProcessor(
        acquisition_store,
        adapter,
        worker_id="trend-input-test-worker",
    )

    assert outbox.run_once()
    imported = acquisition_store.get_bundle(str(bundle["id"]))
    assert imported is not None and imported["status"] == "imported"
    run_id = str(imported["analysis_run_id"])
    staged = adapter.records_for_run(run_id, "policy")
    assert len(staged) == 1
    assert staged[0].external_id.startswith("gov:")
    assert acquisition_store.poll_outbox("processed", limit=10)[0]["aggregate_id"] == bundle["id"]

    replay = acquisition_store.enqueue_outbox(
        "Bundle",
        str(bundle["id"]),
        "bundle_ready",
        {"bundle_id": str(bundle["id"])},
    )
    assert outbox.run_once()
    assert any(
        entry["id"] == replay["id"]
        for entry in acquisition_store.poll_outbox("processed", limit=10)
    )
    with database.sessions() as session:
        assert session.scalar(
            select(func.count()).select_from(TrendInputRecordModel).where(
                TrendInputRecordModel.bundle_id == str(bundle["id"])
            )
        ) == 1
        assert session.get(AnalysisRunModel, run_id).status == "pending"

    data_store = SqlAlchemyAnalysisDataStore(database.sessions)
    repository = SqlAlchemyAnalysisRunRepository(database.sessions)
    pipeline = MarketPrediction(
        data_store,
        [],
        StaticExtractor(),
        credibility_store,
        trend_input_adapter=adapter,
    )
    worker = AnalysisWorker(
        repository,
        worker_id="analysis-bundle-test-worker",
        lease_seconds=30,
        retry_delay_seconds=0,
        executor=pipeline.execute,
    )
    assert worker.run_once()
    assert repository.get(run_id).status.value == "succeeded"
    assert data_store.predictions(run_id)
    with database.sessions() as session:
        assert session.scalar(
            select(func.count()).select_from(SourceSnapshotModel).where(
                SourceSnapshotModel.first_seen_run_id == run_id
            )
        ) == 1


def test_contract_failure_keeps_bundle_ready_and_outbox_retryable(database):
    acquisition_store = SqlAlchemyAcquisitionStore(database.sessions)
    bundle = create_ready_bundle(acquisition_store)
    with database.sessions.begin() as session:
        row = session.get(AcquisitionBundleModel, str(bundle["id"]))
        row.payload = {**row.payload, "record_count": 999}

    adapter = SqlAlchemyTrendInputAdapter(database.sessions)
    outbox = OutboxProcessor(
        acquisition_store,
        adapter,
        worker_id="trend-input-failure-worker",
    )
    assert outbox.run_once()

    failed_bundle = acquisition_store.get_bundle(str(bundle["id"]))
    assert failed_bundle is not None
    assert failed_bundle["status"] == "ready"
    assert failed_bundle["analysis_run_id"] is None
    pending = acquisition_store.poll_outbox("pending", limit=10)
    assert len(pending) == 1
    assert pending[0]["retry_count"] == 1
    assert "does not match persisted contract" in str(pending[0]["error_message"])
    with database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(TrendInputRecordModel)) == 0
        assert session.scalar(select(func.count()).select_from(AnalysisRunModel)) == 0
