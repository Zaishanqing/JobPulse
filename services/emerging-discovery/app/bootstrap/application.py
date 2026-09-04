from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from jobgraph_contracts.offline_api_docs import install_offline_api_docs

from app.api.router import router
from app.application.candidate_lifecycle import SyncCandidateLifecycle
from app.application.contracts import DiscoveryContractConflict
from app.application.discovery import RunDiscovery
from app.application.comparison import CompareAlgorithms
from app.application.offline_evaluation import EvaluateAlgorithmsOffline
from app.application.handlers import (
    DiscoveryHandlers,
    InternalServiceAuthenticator,
    QueryDiscovery,
)
from app.application.maintenance import PurgeDiscoveryRun
from app.application.recompute import RecomputeEmergingConclusion
from app.bootstrap.settings import Settings, settings
from app.infrastructure.database import Database, create_database
from app.infrastructure.providers import (
    DomainLineageMatcher,
    KnowledgeGraphPositionReferenceProvider,
    PayloadPositionReferenceProvider,
    PositionReferenceError,
    SelectableDiscoveryAlgorithm,
)
from app.infrastructure.repositories import (
    DiscoveryMaintenanceUnitOfWork,
    SqlAlchemyDiscoveryUnitOfWork,
)
from app.infrastructure.algorithm_registry import AlgorithmRegistry
from app.infrastructure.emergence_v32 import KnowledgeGraphEmergenceV32Client
from app.infrastructure.semantic_embeddings import SemanticProviderUnavailable


def _reference_provider(config: Settings):
    if config.POSITION_REFERENCE_PROVIDER == "knowledge_graph_http":
        return KnowledgeGraphPositionReferenceProvider(
            config.KNOWLEDGE_GRAPH_BASE_URL,
            config.KNOWLEDGE_GRAPH_SERVICE_USERNAME,
            config.KNOWLEDGE_GRAPH_SERVICE_PASSWORD,
            config.KNOWLEDGE_GRAPH_TIMEOUT_SECONDS,
        )
    provider = PayloadPositionReferenceProvider()
    if config.ENVIRONMENT == "production":
        raise RuntimeError("production cannot start with a fake position reference provider")
    return provider


def create_app(
    config: Settings | None = None,
    database: Database | None = None,
) -> FastAPI:
    runtime = config or settings
    app = FastAPI(
        title="Jobgraph Emerging Discovery",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
    )
    install_offline_api_docs(app)
    app.state.database = database or create_database(runtime)
    references = _reference_provider(runtime)
    algorithm = SelectableDiscoveryAlgorithm(
        emergence_v32=KnowledgeGraphEmergenceV32Client(
            runtime.KNOWLEDGE_GRAPH_BASE_URL,
            runtime.KNOWLEDGE_GRAPH_SERVICE_USERNAME,
            runtime.KNOWLEDGE_GRAPH_SERVICE_PASSWORD,
            runtime.KNOWLEDGE_GRAPH_TIMEOUT_SECONDS,
        ),
    )
    comparison = CompareAlgorithms(AlgorithmRegistry())
    offline_evaluation = EvaluateAlgorithmsOffline(comparison)

    @app.middleware("http")
    async def application_scope(request: Request, call_next):
        session = app.state.database.session_factory()
        discovery_uow = SqlAlchemyDiscoveryUnitOfWork(session)
        request.state.discovery_handlers = DiscoveryHandlers(
            RunDiscovery(
                references,
                algorithm,
                discovery_uow,
                DomainLineageMatcher(),
                candidate_lifecycle=SyncCandidateLifecycle(discovery_uow.candidates),
            ),
            QueryDiscovery(discovery_uow),
            PurgeDiscoveryRun(DiscoveryMaintenanceUnitOfWork(session, runtime.MAINTENANCE_TOKEN)),
            InternalServiceAuthenticator(f"Bearer {runtime.INTERNAL_SERVICE_TOKEN}"),
            session.connection,
            comparison,
            offline_evaluation,
            RecomputeEmergingConclusion(references, algorithm),
        )
        try:
            return await call_next(request)
        finally:
            session.close()

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "code": 422,
                "message": "request validation failed",
                "data": {"errors": jsonable_encoder(exc.errors())},
            },
        )

    @app.exception_handler(PositionReferenceError)
    async def position_reference_error(_request: Request, exc: PositionReferenceError):
        return JSONResponse(
            status_code=502,
            content={
                "code": 502,
                "message": "formal position reference is unavailable",
                "data": {
                    "error_code": exc.code,
                    "reason": str(exc),
                    "details": exc.details,
                },
            },
        )

    @app.exception_handler(SemanticProviderUnavailable)
    async def semantic_provider_unavailable(_request: Request, exc: SemanticProviderUnavailable):
        return JSONResponse(
            status_code=503,
            content={
                "code": 503,
                "message": "semantic embedding provider is unavailable",
                "data": {
                    "error_code": "semantic_provider_unavailable",
                    "reason": str(exc),
                },
            },
        )

    @app.exception_handler(DiscoveryContractConflict)
    async def contract_conflict(_request: Request, exc: DiscoveryContractConflict):
        return JSONResponse(
            status_code=409,
            content={"code": 409, "message": str(exc), "data": None},
        )

    @app.exception_handler(ValueError)
    async def evidence_validation_error(_request: Request, exc: ValueError):
        return JSONResponse(
            status_code=422,
            content={"code": 422, "message": str(exc), "data": None},
        )

    app.include_router(router)
    return app
