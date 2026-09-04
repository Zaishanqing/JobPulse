import asyncio
import os
import time
from collections.abc import Mapping
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from jobgraph_contracts.offline_api_docs import install_offline_api_docs
from starlette.responses import JSONResponse

from app.api.router import router
from app.api.security import security_audit
from app.application.authorization import AuthorizationError
from app.application.deepseek_semantic_candidates import (
    DeepSeekSemanticCandidateConfig,
    DeepSeekSemanticCandidateService,
)
from app.application.evaluation import MatchEvaluationService
from app.application.evaluation_tasks import DEFAULT_PERSISTENCE_VERSIONS, EvaluationTaskService
from app.application.explanation_deletion import ExplanationDeletionService
from app.application.health import DependencySpec, HealthService
from app.application.integration import ContractIntegrationService
from app.application.learning_paths import LearningPathService
from app.application.model_artifact import verify_manifest
from app.application.resource_authorization import (
    ResourceAuthorizationService,
    ResourceNotFoundError,
)
from app.application.responsibility_ce import (
    ResponsibilityCEConfig,
    ResponsibilityCEVerifier,
)
from app.application.semantic_retrieval import (
    SemanticRetrievalConfig,
    SemanticRetrievalService,
)
from app.application.task_submission import TaskSubmissionService
from app.application.validation import ProfileValidationService
from app.application.vector_index_admin import VectorIndexAdminService
from app.application.vector_indexing import VectorIndexPlanningService
from app.application.what_if import WhatIfService
from app.infrastructure.authentication import (
    JwtOidcAuthenticationProvider,
    OidcJwksAuthenticationProvider,
    build_authentication_provider,
)
from app.infrastructure.deepseek_semantic_source import DeepSeekSemanticCandidateSource
from app.infrastructure.feature_flag_configuration import build_feature_flags
from app.infrastructure.http_embedding_adapter import HttpEmbeddingAdapter
from app.infrastructure.http_observability import (
    ApiRuntimeState,
    ObservabilityMiddleware,
)
from app.infrastructure.http_retrieval_adapters import (
    HttpRerankerAdapter,
    HttpSparseRetrievalAdapter,
)
from app.infrastructure.http_sources import HttpCVProfileSource, HttpPositionProfileSource
from app.infrastructure.memory_sources import (
    InMemoryCVProfileSource,
    InMemoryPositionProfileSource,
)
from app.infrastructure.metrics import MetricsRegistry
from app.infrastructure.persistence_configuration import build_persistence
from app.infrastructure.qdrant_vector_store import QdrantVectorStoreAdapter
from app.infrastructure.queue_configuration import build_task_queue
from app.infrastructure.redis_task_queue import RedisTaskQueue
from app.infrastructure.relation_sources import HttpSkillRelationSource, InMemorySkillRelationSource
from app.infrastructure.resource_authorization import (
    AllowAllApplicationGrantAdapter,
    AllowAllCVAuthorizationAdapter,
    HttpApplicationGrantAdapter,
    HttpCVAuthorizationAdapter,
    HttpEnterpriseJobGrantAdapter,
)
from app.infrastructure.structured_logging import StructuredLogger
from app.ports.authentication import AuthenticationError, AuthenticationProvider
from app.ports.profile_sources import CVProfileSource, PositionProfileSource
from app.ports.repositories import UnitOfWorkFactory
from app.ports.resource_authorization import ApplicationGrantPort, CVAuthorizationPort
from app.ports.skill_relations import SkillRelationSource
from app.ports.task_queue import TaskQueue


def build_responsibility_verifier(
    source_values: Mapping[str, str],
    *,
    runtime_mode: str,
) -> ResponsibilityCEVerifier | None:
    """Build the frozen responsibility verifier only when explicitly enabled.

    Production must not silently downgrade a requested CE deployment to the
    deterministic matcher. The model path and embedding endpoint are therefore
    validated before the API is considered started.
    """
    mode = source_values.get("MATCHING_RESPONSIBILITY_CE_MODE", "disabled").strip().lower()
    if mode not in {"disabled", "enabled"}:
        raise ValueError("MATCHING_RESPONSIBILITY_CE_MODE must be disabled or enabled")
    if mode == "disabled":
        return None

    model_path = source_values.get("MATCHING_RESPONSIBILITY_CE_MODEL_PATH", "").strip()
    embedding_url = source_values.get(
        "MATCHING_RESPONSIBILITY_CE_EMBEDDING_URL", ""
    ).strip()
    missing = []
    if not model_path:
        missing.append("MATCHING_RESPONSIBILITY_CE_MODEL_PATH")
    if not embedding_url:
        missing.append("MATCHING_RESPONSIBILITY_CE_EMBEDDING_URL")
    if missing:
        raise ValueError("responsibility CE configuration is incomplete: " + ", ".join(missing))

    fallback_to_rules = (
        source_values.get("MATCHING_RESPONSIBILITY_CE_FALLBACK_TO_RULES", "false")
        .strip()
        .lower()
        == "true"
    )
    if runtime_mode == "production" and fallback_to_rules:
        raise ValueError(
            "production responsibility CE cannot enable "
            "MATCHING_RESPONSIBILITY_CE_FALLBACK_TO_RULES"
        )

    artifact_digest: str | None = None
    manifest_path = source_values.get(
        "MATCHING_RESPONSIBILITY_CE_MANIFEST_PATH",
        str(Path(model_path) / "manifest.json"),
    ).strip()
    if runtime_mode == "production":
        try:
            artifact_digest = verify_manifest(model_path, manifest_path)
        except ValueError as exc:
            raise ValueError(f"responsibility CE artifact invalid: {exc}") from exc

    verifier = ResponsibilityCEVerifier(
        ResponsibilityCEConfig(
            model_path=model_path,
            threshold=float(
                source_values.get("MATCHING_RESPONSIBILITY_CE_THRESHOLD", "1.098377")
            ),
            top_k=int(source_values.get("MATCHING_RESPONSIBILITY_CE_TOP_K", "3")),
            max_length=int(
                source_values.get("MATCHING_RESPONSIBILITY_CE_MAX_LENGTH", "256")
            ),
            embedding_url=embedding_url,
            embedding_model=source_values.get(
                "MATCHING_RESPONSIBILITY_CE_EMBEDDING_MODEL", "BAAI/bge-m3"
            ),
            embedding_revision=source_values.get(
                "MATCHING_RESPONSIBILITY_CE_EMBEDDING_REVISION",
                "5617a9f61b028005a4858fdac845db406aefb181",
            ),
            embedding_dimension=int(
                source_values.get("MATCHING_RESPONSIBILITY_CE_EMBEDDING_DIMENSION", "1024")
            ),
            embedding_timeout_seconds=float(
                source_values.get("MATCHING_RESPONSIBILITY_CE_EMBEDDING_TIMEOUT_SECONDS", "30")
            ),
            fallback_to_rules=fallback_to_rules,
            artifact_digest=artifact_digest,
        )
    )
    if runtime_mode == "production" and not verifier.model_loaded:
        raise ValueError(
            "responsibility CE model failed to load: "
            + (verifier.model_load_error or "unknown error")
        )
    return verifier


def create_app(
    *,
    cv_source: CVProfileSource | None = None,
    position_source: PositionProfileSource | None = None,
    relation_source: SkillRelationSource | None = None,
    persistence_env: Mapping[str, str] | None = None,
    queue_env: Mapping[str, str] | None = None,
    unit_of_work: UnitOfWorkFactory | None = None,
    task_queue: TaskQueue | None = None,
    metrics_registry: MetricsRegistry | None = None,
    structured_logger: StructuredLogger | None = None,
    runtime_env: Mapping[str, str] | None = None,
    authentication_provider: AuthenticationProvider | None = None,
    auth_env: Mapping[str, str] | None = None,
    cv_authorization: CVAuthorizationPort | None = None,
    application_grants: ApplicationGrantPort | None = None,
    vector_index_admin_service: VectorIndexAdminService | None = None,
    semantic_retrieval_service: SemanticRetrievalService | None = None,
    semantic_candidate_service: DeepSeekSemanticCandidateService | None = None,
) -> FastAPI:
    metrics_registry = metrics_registry or MetricsRegistry()
    structured_logger = structured_logger or StructuredLogger()
    runtime = ApiRuntimeState()
    runtime_values = os.environ if runtime_env is None else runtime_env
    runtime_mode = (
        runtime_values.get(
            "MATCHING_RUNTIME_MODE", os.getenv("MATCHING_RUNTIME_MODE", "production")
        )
        .strip()
        .lower()
    )
    if runtime_mode not in {"production", "development", "test"}:
        raise ValueError("MATCHING_RUNTIME_MODE must be production, development or test")
    persistence_values = os.environ if persistence_env is None else persistence_env
    queue_values = os.environ if queue_env is None else queue_env
    auth_values = os.environ if auth_env is None else auth_env
    source_values = dict(os.environ)
    source_values.update(runtime_values)
    stage_e_feature_flags = (
        build_feature_flags(source_values)
        if any(key.startswith("MATCHING_FF_") for key in source_values)
        else None
    )
    if runtime_mode == "production":
        required = (
            "MATCHING_CV_SOURCE_URL",
            "MATCHING_POSITION_SOURCE_URL",
            "MATCHING_GRAPH_SOURCE_URL",
            "MATCHING_GRAPH_VERSION",
            "MATCHING_CV_AUTHORIZATION_URL",
            "MATCHING_APPLICATION_GRANT_URL",
            "MATCHING_UPSTREAM_SERVICE_TOKEN",
        )
        missing = [name for name in required if not source_values.get(name, "").strip()]
        if (
            persistence_values.get("MATCHING_PERSISTENCE_PROVIDER", "").strip().lower()
            != "postgres"
        ):
            missing.append("MATCHING_PERSISTENCE_PROVIDER=postgres")
        if queue_values.get("MATCHING_QUEUE_PROVIDER", "").strip().lower() != "redis":
            missing.append("MATCHING_QUEUE_PROVIDER=redis")
        if auth_values.get("MATCHING_AUTH_MODE", "").strip().lower() not in {
            "jwt",
            "oidc",
        }:
            missing.append("MATCHING_AUTH_MODE=jwt|oidc")
        if missing:
            raise ValueError("production configuration is incomplete: " + ", ".join(missing))
    shutdown_timeout = float(runtime_values.get("MATCHING_API_SHUTDOWN_TIMEOUT_SECONDS", "30"))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        runtime.accepting_requests = False
        deadline = time.monotonic() + max(shutdown_timeout, 0)
        while runtime.active_requests and time.monotonic() < deadline:
            await asyncio.sleep(0.01)

    application = FastAPI(
        title="人岗匹配画像服务",
        version="0.14.0",
        openapi_url="/api/v1/openapi.json",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    install_offline_api_docs(application)
    application.state.metrics_registry = metrics_registry
    application.state.structured_logger = structured_logger
    application.state.api_runtime = runtime
    application.state.runtime_mode = runtime_mode
    application.state.vector_index_admin_service = vector_index_admin_service
    application.state.authentication_provider = (
        authentication_provider or build_authentication_provider(auth_env)
    )
    if runtime_mode == "production" and not isinstance(
        application.state.authentication_provider,
        JwtOidcAuthenticationProvider | OidcJwksAuthenticationProvider,
    ):
        raise ValueError("production requires JWT/OIDC authentication")

    @application.exception_handler(AuthenticationError)
    async def authentication_error(_: Request, exc: AuthenticationError) -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content={"code": exc.code, "message": exc.message},
            headers={"WWW-Authenticate": "Bearer"},
        )

    @application.exception_handler(AuthorizationError)
    async def authorization_error(_: Request, exc: AuthorizationError) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={"code": exc.code, "message": exc.message},
        )

    @application.exception_handler(ResourceNotFoundError)
    async def resource_not_found(request: Request, exc: ResourceNotFoundError) -> JSONResponse:
        security_context = getattr(request.state, "auth_context", None)
        security_audit(
            request,
            decision="denied",
            reason_code=exc.code,
            context=security_context,
        )
        return JSONResponse(
            status_code=404,
            content={"code": exc.code, "message": exc.message},
        )

    application.add_middleware(
        ObservabilityMiddleware,
        metrics=metrics_registry,
        logger=structured_logger,
        runtime=runtime,
    )
    profile_validation_service = ProfileValidationService()
    application.state.profile_validation_service = profile_validation_service
    semantic_mode = source_values.get("MATCHING_SEMANTIC_MODE", "disabled").strip().lower()
    semantic_demo = source_values.get("MATCHING_SEMANTIC_DEMO", "false").strip().lower() == "true"
    if semantic_demo and semantic_mode == "enabled":
        raise ValueError("competition semantic demo supports shadow mode only")
    if semantic_mode not in {"disabled", "shadow"}:
        raise ValueError("MATCHING_SEMANTIC_MODE must be disabled or shadow")
    if semantic_demo and semantic_mode != "shadow":
        raise ValueError("competition semantic demo requires shadow mode")
    if semantic_retrieval_service is None and semantic_mode != "disabled":
        dense_enabled = source_values.get("MATCHING_DENSE_ENABLED", "true").lower() == "true"
        sparse_enabled = source_values.get("MATCHING_SPARSE_ENABLED", "false").lower() == "true"
        reranker_enabled = source_values.get("MATCHING_RERANKER_ENABLED", "false").lower() == "true"
        if semantic_demo and (not dense_enabled or sparse_enabled or reranker_enabled):
            raise ValueError(
                "competition semantic demo requires dense=true, sparse=false and reranker=false"
            )
        model = source_values.get("MATCHING_VECTOR_EMBEDDING_MODEL", "").strip()
        revision = source_values.get("MATCHING_VECTOR_EMBEDDING_REVISION", "").strip()
        dimension = int(source_values.get("MATCHING_QDRANT_DIMENSION", "0"))
        collection = source_values.get(
            "MATCHING_QDRANT_COLLECTION", "matching_fragments_v1"
        ).strip()
        retrieval_embedding = HttpEmbeddingAdapter(
            source_values.get("MATCHING_EMBEDDING_ENDPOINT", ""),
            model=model,
            revision=revision,
            dimension=dimension,
            timeout_seconds=float(source_values.get("MATCHING_EMBEDDING_TIMEOUT_SECONDS", "10")),
        )
        retrieval_vectors = QdrantVectorStoreAdapter(
            source_values.get("MATCHING_QDRANT_URL", ""),
            api_key=source_values.get("MATCHING_QDRANT_API_KEY") or None,
            collection_name=collection,
            index_revision=source_values.get("MATCHING_VECTOR_INDEX_REVISION", collection),
            dimension=dimension,
            timeout_seconds=float(source_values.get("MATCHING_QDRANT_TIMEOUT_SECONDS", "5")),
            max_retries=int(source_values.get("MATCHING_QDRANT_MAX_RETRIES", "2")),
            retry_backoff_seconds=float(
                source_values.get("MATCHING_QDRANT_RETRY_BACKOFF_SECONDS", "0.1")
            ),
        )
        if runtime_mode == "production":
            retrieval_embedding.check_startup_contract()
            retrieval_vectors.check_startup_contract()
        sparse = (
            HttpSparseRetrievalAdapter(
                source_values.get("MATCHING_SPARSE_ENDPOINT", ""),
                api_key=source_values.get("MATCHING_SPARSE_API_KEY") or None,
                health_endpoint=source_values.get("MATCHING_SPARSE_HEALTH_ENDPOINT") or None,
                timeout_seconds=float(source_values.get("MATCHING_SPARSE_TIMEOUT_SECONDS", "5")),
            )
            if sparse_enabled
            else None
        )
        reranker = (
            HttpRerankerAdapter(
                source_values.get("MATCHING_RERANKER_ENDPOINT", ""),
                api_key=source_values.get("MATCHING_RERANKER_API_KEY") or None,
                health_endpoint=source_values.get("MATCHING_RERANKER_HEALTH_ENDPOINT") or None,
                timeout_seconds=float(source_values.get("MATCHING_RERANKER_TIMEOUT_SECONDS", "2")),
            )
            if reranker_enabled
            else None
        )
        semantic_retrieval_service = SemanticRetrievalService(
            retrieval_embedding,
            retrieval_vectors,
            SemanticRetrievalConfig(
                mode=semantic_mode,
                embedding_model=model,
                embedding_revision=revision,
                embedding_dimension=dimension,
                index_revision=source_values.get("MATCHING_VECTOR_INDEX_REVISION", collection),
                collection=collection,
                semantic_weight=float(
                    source_values.get("MATCHING_SEMANTIC_WEIGHT", "0" if semantic_demo else "0.15")
                ),
                top_k_per_fragment=int(source_values.get("MATCHING_SEMANTIC_TOP_K", "20")),
                top_n_candidates=int(source_values.get("MATCHING_SEMANTIC_TOP_N", "50")),
                dense_enabled=dense_enabled,
                sparse_enabled=sparse_enabled,
                dense_rrf_weight=float(source_values.get("MATCHING_DENSE_RRF_WEIGHT", "0.7")),
                sparse_rrf_weight=float(source_values.get("MATCHING_SPARSE_RRF_WEIGHT", "0.3")),
                rrf_k=int(source_values.get("MATCHING_RRF_K", "30")),
                fusion_top_k=int(source_values.get("MATCHING_HYBRID_TOP_K", "10")),
                hybrid_threshold=float(source_values.get("MATCHING_HYBRID_THRESHOLD", "0.7")),
                reranker_enabled=reranker_enabled,
                reranker_model_revision=source_values.get(
                    "MATCHING_RERANKER_MODEL_REVISION", "reranker.disabled"
                ),
                reranker_top_k=int(source_values.get("MATCHING_RERANKER_TOP_K", "50")),
                reranker_top_n=int(source_values.get("MATCHING_RERANKER_TOP_N", "10")),
                max_latency_ms=float(source_values.get("MATCHING_ROLLBACK_LATENCY_MS", "1000")),
                disabled_tenant_refs=frozenset(
                    item.strip()
                    for item in source_values.get(
                        "MATCHING_SEMANTIC_DISABLED_TENANT_REFS", ""
                    ).split(",")
                    if item.strip()
                ),
                disabled_target_types=frozenset(
                    item.strip()
                    for item in source_values.get(
                        "MATCHING_SEMANTIC_DISABLED_TARGET_TYPES", ""
                    ).split(",")
                    if item.strip()
                ),
            ),
            sparse=sparse,
            reranker=reranker,
            feature_flags=stage_e_feature_flags,
            metrics=metrics_registry,
        )
    if semantic_retrieval_service is not None:
        semantic_mode = semantic_retrieval_service.config.mode
    deepseek_mode = source_values.get("MATCHING_DEEPSEEK_MODE", "disabled").strip().lower()
    if semantic_candidate_service is None and deepseek_mode != "disabled":
        deepseek_model = source_values.get("MATCHING_DEEPSEEK_MODEL", "deepseek-v4-flash").strip()
        deepseek_algorithm = source_values.get(
            "MATCHING_DEEPSEEK_ALGORITHM_VERSION",
            "deepseek-semantic-candidates.v1",
        ).strip()
        semantic_candidate_service = DeepSeekSemanticCandidateService(
            DeepSeekSemanticCandidateSource(
                model=deepseek_model,
                algorithm_version=deepseek_algorithm,
                timeout_seconds=int(source_values.get("MATCHING_DEEPSEEK_TIMEOUT_SECONDS", "120")),
                max_candidates=int(source_values.get("MATCHING_DEEPSEEK_MAX_CANDIDATES", "10")),
            ),
            DeepSeekSemanticCandidateConfig(
                mode=deepseek_mode,
                algorithm_version=deepseek_algorithm,
                minimum_relation_confidence=float(
                    source_values.get("MATCHING_DEEPSEEK_MIN_RELATION_CONFIDENCE", "0.7")
                ),
                max_candidates_per_requirement=int(
                    source_values.get("MATCHING_DEEPSEEK_MAX_CANDIDATES_PER_REQUIREMENT", "3")
                ),
            ),
        )
    responsibility_verifier = build_responsibility_verifier(
        source_values,
        runtime_mode=runtime_mode,
    )
    scoring_algorithm_version = "explainable-scoring.v4"
    stage_d_config = (
        semantic_retrieval_service.config
        if semantic_retrieval_service is not None and semantic_mode != "disabled"
        else None
    )
    embedding_provider_kind = (
        "http"
        if semantic_retrieval_service is not None and semantic_mode != "disabled"
        else "disabled"
    )
    vector_provider_kind = embedding_provider_kind
    service_token = source_values.get("MATCHING_UPSTREAM_SERVICE_TOKEN")
    upstream_provider = source_values.get("MATCHING_UPSTREAM_PROVIDER", "runtime").strip().lower()
    if upstream_provider not in {"runtime", "http"}:
        raise ValueError("MATCHING_UPSTREAM_PROVIDER must be runtime or http")
    use_http_profiles = runtime_mode == "production" or upstream_provider == "http"
    graph_provider = source_values.get("MATCHING_GRAPH_SOURCE_PROVIDER", "runtime").strip().lower()
    if graph_provider not in {"runtime", "http"}:
        raise ValueError("MATCHING_GRAPH_SOURCE_PROVIDER must be runtime or http")
    use_http_graph = runtime_mode == "production" or graph_provider == "http"
    if upstream_provider == "http":
        required_upstreams = (
            "MATCHING_CV_SOURCE_URL",
            "MATCHING_POSITION_SOURCE_URL",
            "MATCHING_UPSTREAM_SERVICE_TOKEN",
        )
        missing_upstreams = [
            name for name in required_upstreams if not source_values.get(name, "").strip()
        ]
        if missing_upstreams:
            raise ValueError(
                "http upstream configuration is incomplete: " + ", ".join(missing_upstreams)
            )
    if graph_provider == "http":
        required_graph = ("MATCHING_GRAPH_SOURCE_URL", "MATCHING_GRAPH_VERSION")
        missing_graph = [name for name in required_graph if not source_values.get(name, "").strip()]
        if missing_graph:
            raise ValueError("http graph configuration is incomplete: " + ", ".join(missing_graph))
    upstream_timeout = float(source_values.get("MATCHING_UPSTREAM_TIMEOUT_SECONDS", "5"))
    upstream_retries = int(source_values.get("MATCHING_UPSTREAM_MAX_RETRIES", "2"))
    upstream_backoff = float(source_values.get("MATCHING_UPSTREAM_RETRY_BACKOFF_SECONDS", "0.1"))
    if relation_source is None and use_http_graph:
        relation_source = HttpSkillRelationSource(
            source_values["MATCHING_GRAPH_SOURCE_URL"],
            service_token=service_token,
            timeout_seconds=upstream_timeout,
            max_retries=upstream_retries,
            retry_backoff_seconds=upstream_backoff,
            health_url=source_values.get("MATCHING_GRAPH_HEALTH_URL"),
            expected_graph_version=source_values["MATCHING_GRAPH_VERSION"],
        )
    if cv_source is None and use_http_profiles:
        cv_source = HttpCVProfileSource(
            source_values["MATCHING_CV_SOURCE_URL"],
            "/api/v1/contracts/cv-profiles",
            service_token=service_token,
            timeout_seconds=upstream_timeout,
            max_retries=upstream_retries,
            retry_backoff_seconds=upstream_backoff,
            health_url=source_values.get("MATCHING_CV_HEALTH_URL"),
        )
    if position_source is None and use_http_profiles:
        position_source = HttpPositionProfileSource(
            source_values["MATCHING_POSITION_SOURCE_URL"],
            "/api/v1/contracts/position-profiles",
            enterprise_contract_path="/api/v1/contracts/enterprise-job-profiles",
            service_token=service_token,
            timeout_seconds=upstream_timeout,
            max_retries=upstream_retries,
            retry_backoff_seconds=upstream_backoff,
            health_url=source_values.get("MATCHING_POSITION_HEALTH_URL"),
        )
    if cv_authorization is None:
        cv_authorization = (
            HttpCVAuthorizationAdapter(
                source_values["MATCHING_CV_AUTHORIZATION_URL"],
                service_token=source_values["MATCHING_UPSTREAM_SERVICE_TOKEN"],
                timeout_seconds=upstream_timeout,
                max_retries=upstream_retries,
                retry_backoff_seconds=upstream_backoff,
                health_url=source_values.get("MATCHING_CV_AUTHORIZATION_HEALTH_URL"),
            )
            if runtime_mode == "production"
            else AllowAllCVAuthorizationAdapter()
        )
    if application_grants is None:
        application_grants = (
            HttpApplicationGrantAdapter(
                source_values["MATCHING_APPLICATION_GRANT_URL"],
                service_token=source_values["MATCHING_UPSTREAM_SERVICE_TOKEN"],
                timeout_seconds=upstream_timeout,
                max_retries=upstream_retries,
                retry_backoff_seconds=upstream_backoff,
                health_url=source_values.get("MATCHING_APPLICATION_GRANT_HEALTH_URL"),
            )
            if runtime_mode == "production"
            else AllowAllApplicationGrantAdapter()
        )
    enterprise_job_grants = (
        HttpEnterpriseJobGrantAdapter(
            source_values.get(
                "MATCHING_ENTERPRISE_JOB_GRANT_URL",
                source_values["MATCHING_APPLICATION_GRANT_URL"],
            ),
            service_token=source_values["MATCHING_UPSTREAM_SERVICE_TOKEN"],
            timeout_seconds=upstream_timeout,
            max_retries=upstream_retries,
            retry_backoff_seconds=upstream_backoff,
        )
        if runtime_mode == "production"
        else None
    )
    application.state.resource_authorization_service = ResourceAuthorizationService(
        cv_authorization, application_grants, enterprise_job_grants=enterprise_job_grants
    )
    match_evaluation_service = MatchEvaluationService(
        profile_validation_service,
        relation_source=relation_source or InMemorySkillRelationSource(),
        semantic_retrieval=semantic_retrieval_service,
        semantic_candidates=semantic_candidate_service,
        responsibility_verifier=responsibility_verifier,
    )
    application.state.match_evaluation_service = match_evaluation_service
    application.state.responsibility_ce_mode = (
        "enabled" if responsibility_verifier is not None else "disabled"
    )
    application.state.responsibility_ce_verifier = responsibility_verifier
    application.state.what_if_service = WhatIfService(match_evaluation_service)
    learning_path_service = LearningPathService(
        match_evaluation_service,
        expected_scoring_algorithm=scoring_algorithm_version,
    )
    application.state.learning_path_service = learning_path_service
    application.state.explanation_deletion_service = ExplanationDeletionService(
        match_evaluation_service, learning_path_service
    )
    persistence = None
    persistence_provider = "injected"
    if unit_of_work is None:
        persistence_selection = build_persistence(persistence_env)
        persistence = persistence_selection.resource
        unit_of_work = persistence_selection.unit_of_work
        persistence_provider = persistence_selection.provider
    application.state.persistence = persistence
    application.state.persistence_provider = persistence_provider
    task_versions = DEFAULT_PERSISTENCE_VERSIONS.model_copy(
        update={
            "profile_contract_mapping_version": source_values.get(
                "MATCHING_PROFILE_CONTRACT_MAPPING_VERSION", "contract-mapping.v3"
            ),
            "graph_version": source_values.get("MATCHING_GRAPH_VERSION", "graph.disabled"),
            "embedding_model": (
                stage_d_config.embedding_model
                if stage_d_config is not None
                else "embedding.disabled"
            ),
            "embedding_version": (
                stage_d_config.embedding_revision
                if stage_d_config is not None
                else "embedding.disabled"
            ),
            "embedding_dimension": int(
                stage_d_config.embedding_dimension if stage_d_config is not None else 0
            ),
            "vector_text_derivation_version": (
                stage_d_config.vector_text_derivation_version
                if stage_d_config is not None
                else "semantic-fragment.v1"
            ),
            "semantic_algorithm_version": (
                semantic_retrieval_service.algorithm_version
                if stage_d_config is not None and semantic_retrieval_service is not None
                else "semantic-disabled"
            ),
            "semantic_threshold_version": (
                stage_d_config.threshold_config_version
                if stage_d_config is not None
                else "semantic-disabled"
            ),
            "semantic_index_revision": (
                stage_d_config.index_revision
                if stage_d_config is not None
                else source_values.get("MATCHING_VECTOR_INDEX_REVISION", "index.disabled")
            ),
            "scoring_algorithm_version": scoring_algorithm_version,
        }
    )
    evaluation_task_service = EvaluationTaskService(
        unit_of_work,
        match_evaluation_service,
        learning_path_service,
        versions=task_versions,
    )
    application.state.evaluation_task_service = evaluation_task_service
    if task_queue is None:
        queue_selection = build_task_queue(queue_env)
        task_queue = queue_selection.queue
        retry_interval_seconds = queue_selection.retry_interval_seconds
        application.state.queue_provider = queue_selection.provider
    else:
        retry_interval_seconds = 5.0
        application.state.queue_provider = "injected"
        if isinstance(task_queue, RedisTaskQueue):
            application.state.queue_provider = "redis"
    application.state.task_queue = task_queue
    application.state.queue_retry_interval_seconds = retry_interval_seconds
    application.state.task_submission_service = TaskSubmissionService(
        evaluation_task_service,
        task_queue,
        metrics=metrics_registry,
        retry_interval_seconds=retry_interval_seconds,
    )
    health_embedding = None
    health_vector = None
    if semantic_retrieval_service is not None and semantic_mode != "disabled":
        embedding_provider_kind = "http"
        vector_provider_kind = "http"
        health_embedding = semantic_retrieval_service.embedding
        health_vector = semantic_retrieval_service.vectors
    application.state.health_service = HealthService(
        (
            DependencySpec(
                "oidc",
                (
                    "oidc"
                    if isinstance(
                        application.state.authentication_provider,
                        OidcJwksAuthenticationProvider,
                    )
                    else "static"
                ),
                application.state.authentication_provider,
            ),
            DependencySpec("postgresql", persistence_provider, persistence),
            DependencySpec("redis", application.state.queue_provider, task_queue),
            DependencySpec(
                "embedding",
                embedding_provider_kind,
                health_embedding,
            ),
            DependencySpec("vector", vector_provider_kind, health_vector),
            DependencySpec(
                "responsibility_ce",
                "model" if responsibility_verifier is not None else "disabled",
                responsibility_verifier,
            ),
            DependencySpec(
                "sparse",
                "http"
                if semantic_retrieval_service is not None
                and semantic_retrieval_service.config.sparse_enabled
                else "disabled",
                semantic_retrieval_service.sparse
                if semantic_retrieval_service is not None
                else None,
            ),
            DependencySpec(
                "reranker",
                "http"
                if semantic_retrieval_service is not None
                and semantic_retrieval_service.config.reranker_enabled
                else "disabled",
                semantic_retrieval_service.reranker
                if semantic_retrieval_service is not None
                else None,
            ),
            DependencySpec(
                "cv_profile", "http" if runtime_mode == "production" else "disabled", cv_source
            ),
            DependencySpec(
                "position_profile",
                "http" if runtime_mode == "production" else "disabled",
                position_source,
            ),
            DependencySpec(
                "knowledge_graph",
                "http" if runtime_mode == "production" else "disabled",
                relation_source,
            ),
            DependencySpec(
                "cv_authorization",
                "http" if runtime_mode == "production" else "disabled",
                cv_authorization,
            ),
            DependencySpec(
                "application_grant",
                "http" if runtime_mode == "production" else "disabled",
                application_grants,
            ),
        ),
        metrics_registry,
    )
    application.state.contract_integration_service = ContractIntegrationService(
        cv_source or InMemoryCVProfileSource(),
        position_source or InMemoryPositionProfileSource(),
        match_evaluation_service,
        learning_path_service,
    )
    if (
        vector_index_admin_service is None
        and source_values.get("MATCHING_VECTOR_ADMIN_ENABLED", "false").strip().lower() == "true"
    ):
        if cv_source is None or position_source is None:
            raise ValueError("vector admin requires authoritative profile sources")
        revision = source_values.get("MATCHING_VECTOR_EMBEDDING_REVISION", "").strip()
        model = source_values.get("MATCHING_VECTOR_EMBEDDING_MODEL", "").strip()
        qdrant_url = source_values.get("MATCHING_QDRANT_URL", "").strip()
        dimension = int(source_values.get("MATCHING_QDRANT_DIMENSION", "0"))
        if not revision or not model or not qdrant_url or dimension <= 0:
            raise ValueError("vector admin configuration is incomplete")
        admin_vectors = QdrantVectorStoreAdapter(
            qdrant_url,
            api_key=source_values.get("MATCHING_QDRANT_API_KEY") or None,
            collection_name=source_values.get(
                "MATCHING_QDRANT_COLLECTION", "matching_fragments_v1"
            ),
            index_revision=source_values.get(
                "MATCHING_VECTOR_INDEX_REVISION",
                source_values.get("MATCHING_QDRANT_COLLECTION", "matching_fragments_v1"),
            ),
            dimension=dimension,
            timeout_seconds=float(source_values.get("MATCHING_QDRANT_TIMEOUT_SECONDS", "5")),
            max_retries=int(source_values.get("MATCHING_QDRANT_MAX_RETRIES", "2")),
            retry_backoff_seconds=float(
                source_values.get("MATCHING_QDRANT_RETRY_BACKOFF_SECONDS", "0.1")
            ),
        )
        vector_index_admin_service = VectorIndexAdminService(
            unit_of_work=unit_of_work,
            planning=VectorIndexPlanningService(unit_of_work, feature_flags=stage_e_feature_flags),
            cv_source=cv_source,
            position_source=position_source,
            vectors=admin_vectors,
            embedding_model=model,
            embedding_revision=revision,
            embedding_dimension=dimension,
            logger=structured_logger,
        )
    application.state.vector_index_admin_service = vector_index_admin_service
    application.include_router(router)
    return application
