from fastapi import FastAPI
import httpx
from jobgraph_contracts.offline_api_docs import install_offline_api_docs

from app.acquisition.api.acquisition_router import acquisition_router
from app.acquisition.application.crawl_service import CrawlService
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
from app.api.router import router
from app.application.service import AnalysisRunService
from app.application.credibility import CredibilityService
from app.application.evaluation import EvaluationDatasetService
from app.application.trend_change import TrendChangeService
from app.application.trend_history import BuildTrendHistoricalSequence
from app.infrastructure.credibility_store import SqlAlchemyCredibilityStore
from app.infrastructure.evaluation_store import SqlAlchemyEvaluationDatasetStore
from app.infrastructure.source_governance import SqlAlchemySourceGovernanceStore
from app.infrastructure.trend_change_store import SqlAlchemyTrendChangeStore
from app.infrastructure.trend_history_store import SqlAlchemyTrendHistoryStore
from app.infrastructure.database import Database, create_database
from app.infrastructure.market_store import SqlAlchemyAnalysisDataStore
from app.infrastructure.repository import SqlAlchemyAnalysisRunRepository
from app.infrastructure.settings import Settings


def create_app(settings: Settings, *, database: Database | None = None) -> FastAPI:
    db = database or create_database(settings.DATABASE_URL)
    repository = SqlAlchemyAnalysisRunRepository(db.sessions)
    data_store = SqlAlchemyAnalysisDataStore(db.sessions)
    credibility_store = SqlAlchemyCredibilityStore(db.sessions)
    credibility_store.ensure_seeded()
    evaluation_store = SqlAlchemyEvaluationDatasetStore(db.sessions)
    source_governance = SqlAlchemySourceGovernanceStore(db.sessions)
    acquisition_store = SqlAlchemyAcquisitionStore(db.sessions)
    trend_change_store = SqlAlchemyTrendChangeStore(db.sessions)
    trend_history_store = SqlAlchemyTrendHistoryStore(db.sessions)
    app = FastAPI(
        title="Jobgraph Trend Intelligence",
        version="trend-analysis.v2",
        docs_url=None,
        redoc_url=None,
    )
    install_offline_api_docs(app)
    app.state.database = db
    app.state.internal_token = settings.INTERNAL_TOKEN
    app.state.max_upload_size_bytes = settings.MAX_UPLOAD_SIZE_BYTES
    app.state.analysis_service = AnalysisRunService(
        repository, max_attempts=settings.MAX_ATTEMPTS, data_store=data_store,
        credibility_store=credibility_store,
    )
    app.state.evaluation_service = EvaluationDatasetService(evaluation_store)
    app.state.source_governance = source_governance
    app.state.credibility_service = CredibilityService(credibility_store, evaluation_store)
    app.state.acquisition_store = acquisition_store
    app.state.trend_input_adapter = SqlAlchemyTrendInputAdapter(
        db.sessions,
        max_attempts=settings.MAX_ATTEMPTS,
    )
    app.state.trend_change_service = TrendChangeService(
        trend_change_store,
        history_builder=BuildTrendHistoricalSequence(trend_history_store),
    )
    acquisition_client = httpx.Client(
        timeout=settings.HTTP_TIMEOUT_SECONDS,
        proxy=settings.HTTP_PROXY,
        trust_env=False,
        headers={"User-Agent": "JobgraphTrendIntelligence/1.0"},
    )
    acquisition_configurations = credibility_store.payloads(credibility_store.active_versions())
    app.state.crawl_service = CrawlService(
        acquisition_store,
        registry=ConnectorRegistry({
            "arxiv": ArxivConnector(
                acquisition_client,
                acquisition_configurations,
                default_limit=settings.ARXIV_LIMIT,
            ),
            "policy": PolicyConnector(acquisition_client, acquisition_configurations),
            "cvf": CvfConnector(
                acquisition_client,
                acquisition_configurations,
                default_limit=settings.CONFERENCE_LIMIT,
            ),
            "acl": AclConnector(
                acquisition_client,
                acquisition_configurations,
                default_limit=settings.CONFERENCE_LIMIT,
            ),
            "funding": FundingConnector(acquisition_client, acquisition_configurations),
            "github": GithubConnector(acquisition_client, acquisition_configurations),
        }),
    )
    app.include_router(router)
    app.include_router(acquisition_router)
    return app
