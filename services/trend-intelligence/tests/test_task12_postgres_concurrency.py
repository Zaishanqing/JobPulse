from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

from sqlalchemy import func, select

from app.acquisition.application.outbox_processor import OutboxProcessor
from app.acquisition.infrastructure.acquisition_models import (
    AcquisitionBundleModel,
    AcquisitionOutboxModel,
)
from app.acquisition.infrastructure.acquisition_store import SqlAlchemyAcquisitionStore
from app.acquisition.infrastructure.trend_input import SqlAlchemyTrendInputAdapter
from app.api.schemas import CreateAnalysisRunRequest
from app.infrastructure.models import (
    AnalysisRunLogModel,
    AnalysisRunModel,
    TrendInputRecordModel,
)
from app.infrastructure.repository import SqlAlchemyAnalysisRunRepository
from tests.test_trend_input_integration import create_ready_bundle


UTC = timezone.utc


def _command(payload: dict) -> object:
    value = dict(
        payload,
        request_id="task12-postgres-claim",
        idempotency_key="task12-postgres-claim",
    )
    return CreateAnalysisRunRequest.model_validate(value).to_command()


def test_trend_run_has_one_effective_claim_under_concurrency(database, payload):
    assert database.engine.dialect.name == "postgresql"
    repository = SqlAlchemyAnalysisRunRepository(database.sessions)
    run = repository.create_or_get(_command(payload), max_attempts=3)
    claimed_at = datetime.now(UTC)
    start = Barrier(2)

    def claim(worker_id: str) -> tuple[str, str | None]:
        start.wait()
        claimed = repository.claim(
            worker_id,
            now=claimed_at,
            lease=timedelta(seconds=30),
        )
        return worker_id, claimed.id if claimed else None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("trend-worker-a", "trend-worker-b")))

    winner = next(worker_id for worker_id, run_id in results if run_id == run.id)
    assert [run_id for _, run_id in results].count(run.id) == 1
    assert [run_id for _, run_id in results].count(None) == 1

    with database.sessions() as session:
        persisted = session.get(AnalysisRunModel, run.id)
        claim_logs = session.scalars(
            select(AnalysisRunLogModel).where(
                AnalysisRunLogModel.run_id == run.id,
                AnalysisRunLogModel.event == "claimed",
            )
        ).all()
        assert persisted is not None
        assert persisted.status == "running"
        assert persisted.lease_owner == winner
        assert persisted.attempt_count == 1
        assert len(claim_logs) == 1
        assert claim_logs[0].details["worker_id"] == winner

    assert repository.claim(
        "trend-worker-repeat",
        now=claimed_at,
        lease=timedelta(seconds=30),
    ) is None
    with database.sessions() as session:
        assert session.scalar(
            select(func.count()).select_from(AnalysisRunLogModel).where(
                AnalysisRunLogModel.run_id == run.id,
                AnalysisRunLogModel.event == "claimed",
            )
        ) == 1
        assert session.get(AnalysisRunModel, run.id).attempt_count == 1


def test_acquisition_outbox_business_effect_occurs_once_under_concurrency(
    database,
):
    assert database.engine.dialect.name == "postgresql"
    store = SqlAlchemyAcquisitionStore(database.sessions)
    bundle = create_ready_bundle(store)
    adapter = SqlAlchemyTrendInputAdapter(database.sessions, max_attempts=3)
    processors = (
        OutboxProcessor(store, adapter, worker_id="outbox-worker-a", batch_size=1),
        OutboxProcessor(store, adapter, worker_id="outbox-worker-b", batch_size=1),
    )
    processed_at = datetime.now(UTC)
    start = Barrier(2)

    def process(processor: OutboxProcessor) -> bool:
        start.wait()
        return processor.run_once(now=processed_at)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(process, processors))

    assert results.count(True) == 1
    assert results.count(False) == 1
    imported = store.get_bundle(str(bundle["id"]))
    assert imported is not None
    assert imported["status"] == "imported"
    run_id = str(imported["analysis_run_id"])

    with database.sessions() as session:
        outbox = session.scalar(
            select(AcquisitionOutboxModel).where(
                AcquisitionOutboxModel.aggregate_id == str(bundle["id"])
            )
        )
        persisted_bundle = session.get(AcquisitionBundleModel, str(bundle["id"]))
        assert outbox is not None
        assert outbox.status == "processed"
        assert outbox.processed_at is not None
        assert persisted_bundle is not None
        assert persisted_bundle.analysis_run_id == run_id
        assert session.scalar(
            select(func.count()).select_from(TrendInputRecordModel).where(
                TrendInputRecordModel.bundle_id == str(bundle["id"])
            )
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(AnalysisRunModel).where(
                AnalysisRunModel.id == run_id
            )
        ) == 1

    assert [processor.run_once(now=processed_at) for processor in processors] == [
        False,
        False,
    ]
    with database.sessions() as session:
        assert session.scalar(
            select(func.count()).select_from(TrendInputRecordModel).where(
                TrendInputRecordModel.bundle_id == str(bundle["id"])
            )
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(AnalysisRunModel).where(
                AnalysisRunModel.id == run_id
            )
        ) == 1
