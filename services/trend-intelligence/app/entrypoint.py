from __future__ import annotations

import signal
import subprocess
import sys
from threading import Event

import uvicorn
import httpx

from app.application.market_prediction import MarketPrediction
from app.application.position_skill_trend import PositionSkillTrend
from app.application.worker import AnalysisWorker
from app.infrastructure.database import create_database
from app.infrastructure.repository import SqlAlchemyAnalysisRunRepository
from app.infrastructure.keyword_extractor import YakeKeywordExtractor
from app.infrastructure.market_store import SqlAlchemyAnalysisDataStore
from app.infrastructure.credibility_store import SqlAlchemyCredibilityStore
from app.infrastructure.source_governance import SqlAlchemySourceGovernanceStore
from app.infrastructure.settings import Settings
from app.infrastructure.sources import AclSource, ArxivSource, CvfSource, FundingSource, GithubSource, PolicySource
from app.acquisition.application.crawl_service import CrawlService
from app.acquisition.application.outbox_processor import OutboxProcessor
from app.acquisition.infrastructure.acquisition_store import SqlAlchemyAcquisitionStore
from app.acquisition.infrastructure.connectors import (
    AclConnector,
    ArxivConnector,
    ConnectorRegistry,
    CvfConnector,
    FundingConnector,
    GithubConnector,
    PolicyConnector,
)
from app.acquisition.infrastructure.trend_input import SqlAlchemyTrendInputAdapter


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "api"
    settings = Settings()
    if mode == "migrate":
        subprocess.run(["alembic", "upgrade", "head"], check=True)
        return
    if mode == "api":
        subprocess.run(["alembic", "upgrade", "head"], check=True)
        uvicorn.run("app.main:app", host="0.0.0.0", port=8000)
        return
    if mode != "worker":
        raise SystemExit(f"unsupported mode: {mode}")
    database = create_database(settings.DATABASE_URL)
    repository = SqlAlchemyAnalysisRunRepository(database.sessions)
    data_store = SqlAlchemyAnalysisDataStore(database.sessions)
    credibility_store = SqlAlchemyCredibilityStore(database.sessions)
    credibility_store.ensure_seeded()
    source_governance = SqlAlchemySourceGovernanceStore(database.sessions)
    client = httpx.Client(timeout=settings.HTTP_TIMEOUT_SECONDS, proxy=settings.HTTP_PROXY, trust_env=False, headers={"User-Agent": "JobgraphTrendIntelligence/1.0"})
    sources = [
            ArxivSource(client, limit=settings.ARXIV_LIMIT),
            CvfSource(client, limit=settings.CONFERENCE_LIMIT),
            AclSource(client, limit=settings.CONFERENCE_LIMIT),
            PolicySource(client),
            FundingSource(client),
            GithubSource(client, hours=settings.GITHUB_ARCHIVE_HOURS, max_hours=settings.GITHUB_ARCHIVE_MAX_HOURS),
    ]
    extractor = YakeKeywordExtractor()
    trend_input = SqlAlchemyTrendInputAdapter(
        database.sessions,
        max_attempts=settings.MAX_ATTEMPTS,
    )
    pipeline = MarketPrediction(
        data_store,
        sources,
        extractor,
        credibility_store,
        source_governance,
        trend_input,
    )
    skill_pipeline = PositionSkillTrend(
        data_store, sources, extractor, credibility_store, source_governance,
        source_workers=settings.SOURCE_WORKERS,
    )

    def execute(run) -> dict[str, int]:
        return (skill_pipeline if run.run_type == "position_skill_trend" else pipeline).execute(run)
    worker = AnalysisWorker(
        repository,
        worker_id=settings.WORKER_ID,
        lease_seconds=settings.WORKER_LEASE_SECONDS,
        retry_delay_seconds=settings.WORKER_RETRY_DELAY_SECONDS,
        heartbeat_seconds=settings.WORKER_HEARTBEAT_SECONDS,
        executor=execute,
    )
    acquisition_store = SqlAlchemyAcquisitionStore(database.sessions)
    acquisition_configurations = credibility_store.payloads(credibility_store.active_versions())
    acquisition_worker = CrawlService(
        acquisition_store,
        registry=ConnectorRegistry({
            "arxiv": ArxivConnector(
                client,
                acquisition_configurations,
                default_limit=settings.ARXIV_LIMIT,
            ),
            "policy": PolicyConnector(client, acquisition_configurations),
            "cvf": CvfConnector(
                client,
                acquisition_configurations,
                default_limit=settings.CONFERENCE_LIMIT,
            ),
            "acl": AclConnector(
                client,
                acquisition_configurations,
                default_limit=settings.CONFERENCE_LIMIT,
            ),
            "funding": FundingConnector(client, acquisition_configurations),
            "github": GithubConnector(client, acquisition_configurations),
        }),
    )
    outbox_worker = OutboxProcessor(
        acquisition_store,
        trend_input,
        worker_id=f"{settings.WORKER_ID}-acquisition-outbox",
        lease_seconds=settings.WORKER_LEASE_SECONDS,
    )
    stop = Event()

    def request_stop(_signum, _frame) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    while not stop.is_set():
        analysis_progress = worker.run_once()
        acquisition_progress = acquisition_worker.run_once(
            settings.WORKER_ID,
            lease_seconds=settings.WORKER_LEASE_SECONDS,
        )
        outbox_progress = outbox_worker.run_once()
        if not analysis_progress and not acquisition_progress and not outbox_progress:
            stop.wait(settings.WORKER_POLL_SECONDS)


if __name__ == "__main__":
    main()
