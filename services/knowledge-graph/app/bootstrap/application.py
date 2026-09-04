from __future__ import annotations

import uuid
from copy import deepcopy

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from jobgraph_contracts.offline_api_docs import install_offline_api_docs

from app.application import (
    AnalyzeDependenciesUseCase,
    AssessJDQualityUseCase,
    AutoReviewBuildUseCase,
    BuildGraphUseCase,
    BuildJobUseCase,
    BatchReviewTasksUseCase,
    DependencyReferenceUseCase,
    ClaimReviewTaskUseCase,
    CompleteReviewTaskUseCase,
    ConfirmExtractionUseCase,
    CompareBuildWatermarksUseCase,
    CreateMappingCandidateUseCase,
    CreateReviewTaskUseCase,
    ExtractJDUseCase,
    ImportExtractionResultUseCase,
    ImportJDUseCase,
    ImportCapabilitySkillSnapshotUseCase,
    ImportNormalizedResultUseCase,
    ImportPublishedJDFactUseCase,
    ModifyRelationUseCase,
    NormalizeJDUseCase,
    OpenGraphDraftUseCase,
    PublishGraphVersionUseCase,
    RebuildProjectionUseCase,
    ReviewMappingCandidateUseCase,
    ReviewDependencyCandidateUseCase,
    ResolveUnresolvedSkillUseCase,
    RollbackGraphVersionUseCase,
    UpdateAlgorithmConfigUseCase,
    UpsertJDUseCase,
)
from app.application.handlers import ApplicationHandlers
from app.application.identity import IdentityService
from app.config import PROVIDERS, Settings
from app.database import create_database
from app.errors import install_error_handlers
from app.infrastructure.sqlalchemy import (
    SqlAlchemyKnowledgeGraphQueryService,
    SqlAlchemyUnitOfWork,
)
from app.infrastructure.identity import (
    JwtTokenCodec,
    PbkdfPasswordVerifier,
    SqlAlchemyIdentityRepository,
    SqlAlchemySessionIdentityRepository,
)
from app.infrastructure.providers.normalization import NormalizationProviderAdapter, Normalizer
from app.infrastructure.providers.identifiers import UuidSkillIdGenerator
from app.application.build_job_runner import BuildJobRunner
from app.infrastructure.readiness import DatabaseReadiness


def _build_handlers(session) -> ApplicationHandlers:
    # The request owns the session; every handler receives a UoW factory bound
    # to that session so endpoint behavior and atomic transaction boundaries stay unchanged.
    def uow_factory():
        return SqlAlchemyUnitOfWork(lambda: session, close_session=False)
    return ApplicationHandlers(
        ImportCapabilitySkillSnapshotUseCase(uow_factory),
        ImportJDUseCase(uow_factory),
        UpsertJDUseCase(uow_factory),
        ImportPublishedJDFactUseCase(uow_factory),
        ExtractJDUseCase(uow_factory),
        ImportExtractionResultUseCase(uow_factory),
        ConfirmExtractionUseCase(uow_factory),
        NormalizeJDUseCase(
            uow_factory,
            NormalizationProviderAdapter(
                Normalizer(session_factory=lambda: session)
            ),
        ),
        ImportNormalizedResultUseCase(uow_factory),
        AssessJDQualityUseCase(uow_factory),
        ResolveUnresolvedSkillUseCase(uow_factory, UuidSkillIdGenerator()),
        BuildGraphUseCase(uow_factory),
        BuildJobUseCase(uow_factory),
        OpenGraphDraftUseCase(uow_factory),
        CreateReviewTaskUseCase(uow_factory),
        ClaimReviewTaskUseCase(uow_factory),
        CompleteReviewTaskUseCase(uow_factory),
        BatchReviewTasksUseCase(uow_factory),
        AutoReviewBuildUseCase(uow_factory),
        ModifyRelationUseCase(uow_factory),
        PublishGraphVersionUseCase(uow_factory),
        RollbackGraphVersionUseCase(uow_factory),
        UpdateAlgorithmConfigUseCase(uow_factory),
        CreateMappingCandidateUseCase(uow_factory),
        ReviewMappingCandidateUseCase(uow_factory),
        AnalyzeDependenciesUseCase(uow_factory),
        ReviewDependencyCandidateUseCase(uow_factory),
        RebuildProjectionUseCase(uow_factory),
        CompareBuildWatermarksUseCase(uow_factory),
        DependencyReferenceUseCase(uow_factory),
    )


def _build_identity(session, settings: Settings) -> IdentityService:
    return IdentityService(
        SqlAlchemySessionIdentityRepository(session),
        PbkdfPasswordVerifier(),
        JwtTokenCodec(settings.jwt_secret_key),
        settings.service_username,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Compose an isolated application instance from explicit runtime settings."""
    runtime_settings = settings or Settings.from_env()
    app = FastAPI(
        title="岗位能力知识图谱 API",
        version="1.0.0",
        openapi_url="/api/v1/openapi.json",
        docs_url=None,
        redoc_url=None,
    )
    install_offline_api_docs(app)
    app.state.settings = runtime_settings
    app.state.providers = deepcopy(PROVIDERS)
    app.state.providers["jwt_auth"] = {
        "status": (
            "development_default"
            if runtime_settings.uses_development_jwt_secret
            else "configured"
        ),
        "secure_for_production": not runtime_settings.uses_development_jwt_secret,
    }
    app.state.database = create_database(runtime_settings)
    app.state.readiness = DatabaseReadiness(app.state.database.engine, runtime_settings)
    app.state.identity_service = IdentityService(
        SqlAlchemyIdentityRepository(app.state.database.session_factory),
        PbkdfPasswordVerifier(),
        JwtTokenCodec(runtime_settings.jwt_secret_key),
        runtime_settings.service_username,
    )
    app.state.identity_builder = lambda session: _build_identity(
        session, runtime_settings
    )
    app.state.command_builder = _build_handlers
    app.state.query_builder = SqlAlchemyKnowledgeGraphQueryService
    app.state.request_session_factory = app.state.database.session_factory
    app.state.close_request_sessions = True
    def worker_uow_factory():
        return SqlAlchemyUnitOfWork(app.state.database.session_factory)
    app.state.build_job_runner = BuildJobRunner(
        BuildJobUseCase(worker_uow_factory),
        BuildGraphUseCase(worker_uow_factory),
        runtime_settings.build_job_worker_id,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=500)
    install_error_handlers(app)

    @app.middleware("http")
    async def application_scope(request: Request, call_next):
        # The composition root owns the request-scoped database resource and
        # exposes only assembled Application services to the API layer.
        session = app.state.request_session_factory()
        request.state.application_handlers = app.state.command_builder(session)
        request.state.query_service = app.state.query_builder(session)
        request.state.identity_service = app.state.identity_builder(session)
        try:
            return await call_next(request)
        finally:
            if app.state.close_request_sessions:
                session.close()

    @app.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = request.headers.get(
            "X-Trace-Id", f"req_{uuid.uuid4().hex[:16]}"
        )
        response = await call_next(request)
        response.headers["X-Trace-Id"] = request.state.trace_id
        return response

    return app
