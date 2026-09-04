from __future__ import annotations

from dataclasses import dataclass

from app.application_container import ApplicationContainer

from app.contexts.access import (
    AccountHandlers,
    AuthenticateAccount,
    ChangePassword,
    ManageAccount,
    ManageEnterprise,
    RegisterAccount,
)
from app.contexts.talent_acquisition import (
    BrowsePublishedJobs,
    ManageRecruitmentJobs,
    RecruitmentHandlers,
)
from app.contexts.discovery import (
    DeletePositionCluster,
    DiscoveryCandidateHandlers,
    PositionDiscoveryHandlers,
    QueryDiscoveryCandidates,
    QueryPositionDiscovery,
    StartPositionDiscovery,
)
from app.contexts.emerging_positions import (
    CreateEmergingCandidate,
    DeleteEmergingCandidate,
    EmergingPositionHandlers,
    GenerateEmergingDefinition,
    ImportFormalExperimentResults,
    PromoteEmergingCandidate,
    PublishEmergingCandidate,
    ReviewEmergingDefinition,
    QueryDefinitionVersions,
    QueryEmergingCandidates,
    QueryGerminationAssessment,
    SelectDefinitionVersion,
    SubmitEmergingDefinition,
    UpdateEmergingCandidate,
)
from app.contexts.jd_lifecycle import JDUseCases
from app.contexts.governance_feedback import GovernanceHandlers, ManageEvidence, ManageRag, ManageReviews
from app.contexts.platform import (
    ManageOutboxEvents,
    ManageSystemConfigs,
    QuerySystemStatus,
)
from app.contexts.market_intelligence import ManagePredictedPositions
from app.contexts.platform import ManageFiles
from app.contexts.tasks import ManageTasks
from app.contexts.acquisition import AcquisitionUseCases
from app.contexts.source_jds import SourceJDUseCases
from app.contexts.extraction_tasks import ExtractionTaskUseCases, RunPendingExtractionTasks
from app.contexts.cv_ingestion import CVIngestionUseCases, RunPendingCVExtractionTasks
from app.contexts.data_validation import CVValidationPolicy, CVValidatorSet
from app.contexts.integration_status import QueryIntegrationStatus
from app.contexts.matching_learning import ManageLearningPaths, ManageMatching
from app.contexts.insight_cards.matching_scenarios import (
    ManageMatchingScenarios,
)
from app.contexts.matching_learning.contracts_service import MatchingContractService
from app.contexts.talent_acquisition import ManageResumes
from app.contexts.catalog import ManageSkills
from app.contexts.catalog import ManagePositions
from app.contexts.evaluation import ManageEvaluation
from app.contexts.market_intelligence import ManageTrendReports
from app.contexts.governance_feedback import ManageFeedback
from app.contexts.platform import ManageOCR
from app.contexts.knowledge_graph import ManageKnowledgeGraphIntegration
from app.contexts.platform import ManageEmbeddings
from app.contexts.talent_acquisition import ManageCandidates
from app.core.config import Settings
from app.core.database import Database, create_database
from app.domain.accounts import AccountActor
from app.domain.errors import PermissionDenied
from app.profile_index_events import (
    PLATFORM_PUBLIC_TENANT_REF,
    personal_tenant_ref,
    tenant_ref,
)
from app.contexts.evidence_rag import ManageEvidenceRag
from app.infrastructure.accounts import (
    JwtTokenAdapter,
    Pbkdf2PasswordAdapter,
    SqlAlchemyAccountUnitOfWork,
)
from app.infrastructure.recruitment import SqlAlchemyRecruitmentUnitOfWork
from app.infrastructure.discovery import (
    HttpEmergingDiscoveryGateway,
    SqlAlchemyDiscoveryUnitOfWork,
)
from app.infrastructure.discovery_datasets import (
    formal_discovery_experiment_clusters,
    formal_discovery_experiment_report,
)
from app.infrastructure.emerging_positions import SqlAlchemyEmergingPositionUnitOfWork
from app.infrastructure.jd_repository import SqlAlchemyJDUoW
from app.infrastructure.governance import EvidenceRetrieverAdapter, SqlAlchemyGovernanceUnitOfWork
from app.contexts.insight_cards.internal_adapters import (
    SqlAlchemyEvidenceReadAdapter,
    SqlAlchemyReviewChainAdapter,
)
from app.infrastructure.what_if_scenarios import (
    SqlAlchemyWhatIfScenarioUnitOfWork,
)
from app.infrastructure.system import (
    SqlAlchemySystemConfigUnitOfWork,
    SqlAlchemySystemStatusAdapter,
)
from app.infrastructure.trends import SqlAlchemyTrendUnitOfWork
from app.infrastructure.trend_intelligence_gateway import (
    DisabledTrendIntelligenceGatewayV1,
    HttpTrendIntelligenceGatewayV1,
    TestTrendIntelligenceGatewayV1,
    TrendIntelligenceHttpClient,
)
from app.infrastructure.files import SqlAlchemyFileUnitOfWork
from app.infrastructure.tasks import SqlAlchemyTaskUnitOfWork
from app.infrastructure.acquisition import SqlAlchemyAcquisitionUnitOfWork
from app.infrastructure.crawler_gateway import HttpCrawlerGateway, LocalBundleStore
from app.offline_import.importer import OfflineBundleImporter
from app.offline_import.repository import OfflineImportRepository
from app.infrastructure.matching import SqlAlchemyMatchingUnitOfWork
from app.infrastructure.matching_contracts import (
    KnowledgeGraphMatchingContractReader,
    SqlAlchemyMatchingContractReader,
)
from app.infrastructure.matching_service import (
    DisabledMatchingServiceAdapter,
    HttpMatchingServiceAdapter,
    SqlAlchemyMatchingIdentityAdapter,
)
from app.infrastructure.resume_profiles import SqlAlchemyResumeProfileAdapter
from app.infrastructure.position_profiles import KnowledgeGraphPositionProfileAdapter
from app.infrastructure.matching_positions import SqlAlchemyMatchingPositionCatalog
from app.infrastructure.resumes import (
    IntegrationResumeInputExtractor,
    SqlAlchemyResumeUnitOfWork,
)
from app.infrastructure.skills import SqlAlchemySkillUnitOfWork
from app.infrastructure.positions import SqlAlchemyPositionUnitOfWork
from app.infrastructure.evaluation import SqlAlchemyEvaluationUnitOfWork
from app.infrastructure.trend_analysis import SqlAlchemyTrendAnalysisUnitOfWork
from app.infrastructure.feedback import SqlAlchemyFeedbackUnitOfWork
from app.infrastructure.ocr import IntegrationOCRExtractor, SqlAlchemyOCRUnitOfWork
from app.infrastructure.embedding_gateway import IntegrationEmbeddingGateway, IntegrationVectorGateway
from app.infrastructure.jd_embedding_source import SqlAlchemyJDEmbeddingSource
from app.infrastructure.jd_export import OpenPyxlJDExporter
from app.infrastructure.jd_schema import VersionedJDSchemaAdapter
from app.infrastructure.resume_embedding_source import SqlAlchemyResumeEmbeddingSource
from app.infrastructure.skill_embedding_source import SqlAlchemySkillEmbeddingSource
from app.infrastructure.position_embedding_source import SqlAlchemyPositionEmbeddingSource, SqlAlchemyRelationEmbeddingSource
from app.infrastructure.evidence_embedding_source import SqlAlchemyEvidenceEmbeddingSource
from app.infrastructure.evidence_rag_embedding import HttpEvidenceRagEmbedding
from app.infrastructure.catalog_embedding import HttpCatalogEmbedding
from app.infrastructure.evidence_rag_llm import DeepSeekEvidenceRagLlm
from app.infrastructure.evidence_rag_store import QdrantEvidenceRagStore
from app.infrastructure.evidence_citations import (
    SqlAlchemyEvidenceCitationTargetResolver,
)
from app.infrastructure.evidence_rag_auto_index import (
    RagIndexStatusService,
    RagPositionProfileService,
)
from app.infrastructure.candidates import SqlAlchemyCandidateUnitOfWork
from app.infrastructure.knowledge_graph import KnowledgeGraphAdapterFactory
from app.integrations.knowledge_graph.client import KnowledgeGraphClient
from app.infrastructure.candidate_job_profiles import SqlAlchemyCandidateJobProfileAdapter
from app.infrastructure.candidate_resume_profiles import SqlAlchemyCandidateResumeProfileAdapter
from app.infrastructure.source_jds import SqlAlchemySourceJDUnitOfWork
from app.infrastructure.extraction_tasks import (
    HttpJDExtractionProvider,
    RuleBasedJDExtractionProvider,
    SqlAlchemyExtractionTaskUnitOfWork,
)
from app.infrastructure.cv_ingestion import (
    ApplicationResumeImporter,
    HttpCVExtractionProvider,
    SqlAlchemyCVIngestionUnitOfWork,
)
from app.infrastructure.cv_file_extraction import CVFileTextExtractionAdapter
from app.infrastructure.integration_status import SqlAlchemyIntegrationStatusReader
from app.infrastructure.data_validation import SqlAlchemyValidationPortFactory
from app.workers.extraction_tasks import ExtractionTaskWorker
from app.workers.cv_extraction import CVExtractionWorker
from app.workers.trend_analysis_sync import TrendAnalysisSynchronizer
from app.infrastructure.trend_sync_leadership import PostgresTrendSyncLeadership
from app.infrastructure.outbox import SqlAlchemyOutboxOperationsUnitOfWork
from app.integrations.registry import get_integration_registry
from app.integrations.local import (
    DocxTextDocumentParser,
    PdfTextDocumentParser,
)
from app.contexts.discovery import DiscoveryGateway


@dataclass(frozen=True)
class _ApplicationRuntime:
    database: Database
    container: ApplicationContainer
    jd_worker_enabled: bool
    cv_worker_enabled: bool
    trend_synchronizer: TrendAnalysisSynchronizer
    trend_sync_enabled: bool

    def start(self) -> None:
        if self.jd_worker_enabled:
            self.container.extraction_worker.start()
        if self.cv_worker_enabled:
            self.container.cv_extraction_worker.start()
        if self.trend_sync_enabled:
            self.trend_synchronizer.start()

    def close(self) -> None:
        self.trend_synchronizer.stop()
        self.container.extraction_worker.stop()
        self.container.cv_extraction_worker.stop()
        self.database.dispose()


def _build_runtime(settings: Settings) -> _ApplicationRuntime:
    """Create resources owned exclusively by the service composition root."""

    runtime_database = create_database(settings.DATABASE_URL)
    try:
        container = _build_application_container(settings, runtime_database)
    except Exception:
        runtime_database.dispose()
        raise
    if (
        settings.RAG_EVIDENCE_ENABLED
        and settings.KNOWLEDGE_GRAPH_RAG_DATABASE_URL
    ):
        from app.infrastructure.evidence_rag_auto_index import (
            PublishedGraphVersionIndexer,
            start_index_backfill,
        )

        start_index_backfill(
            PublishedGraphVersionIndexer(
                kg_database_url=settings.KNOWLEDGE_GRAPH_RAG_DATABASE_URL,
                main_database_url=settings.DATABASE_URL,
                rag=container.evidence_rag,
            )
        )
    runtime = _ApplicationRuntime(
        runtime_database,
        container,
        settings.JD_EXTRACTION_WORKER_ENABLED,
        settings.CV_EXTRACTION_WORKER_ENABLED,
        TrendAnalysisSynchronizer(
            container.trend_reports,
            leadership=PostgresTrendSyncLeadership(runtime_database.engine),
            poll_interval_seconds=settings.TREND_ANALYSIS_SYNC_POLL_SECONDS,
        ),
        settings.TREND_INTELLIGENCE_ENABLED,
    )
    runtime.start()
    return runtime


def _build_application_container(
    settings: Settings,
    database: Database,
    discovery_gateway: DiscoveryGateway | None = None,
) -> ApplicationContainer:
    def account_uow() -> SqlAlchemyAccountUnitOfWork:
        return SqlAlchemyAccountUnitOfWork(database.session_factory)

    passwords = Pbkdf2PasswordAdapter()
    tokens = JwtTokenAdapter(settings)
    def jd_uow() -> SqlAlchemyJDUoW:
        return SqlAlchemyJDUoW(
            database.session_factory,
            data_validation_mode=settings.DATA_VALIDATION_MODE,
        )

    def discovery_uow() -> SqlAlchemyDiscoveryUnitOfWork:
        return SqlAlchemyDiscoveryUnitOfWork(
            database.session_factory,
            allow_legacy_reviewed=settings.DISCOVERY_ALLOW_LEGACY_REVIEWED,
        )

    def emerging_uow() -> SqlAlchemyEmergingPositionUnitOfWork:
        return SqlAlchemyEmergingPositionUnitOfWork(database.session_factory)
    registry = get_integration_registry()
    rag_embedding = None
    rag_store = None
    rag_llm = None
    normalization_embedding = None
    if settings.NORMALIZATION_SEMANTIC_ENABLED:
        normalization_embedding = HttpCatalogEmbedding(
            settings.NORMALIZATION_EMBEDDING_URL,
            model=settings.NORMALIZATION_EMBEDDING_MODEL,
            revision=settings.NORMALIZATION_EMBEDDING_REVISION,
            dimension=settings.NORMALIZATION_EMBEDDING_DIMENSION,
            timeout_seconds=settings.NORMALIZATION_EMBEDDING_TIMEOUT_SECONDS,
        )
    if settings.RAG_EVIDENCE_ENABLED:
        rag_embedding = HttpEvidenceRagEmbedding(
            settings.RAG_EVIDENCE_EMBEDDING_URL,
            model=settings.RAG_EVIDENCE_EMBEDDING_MODEL,
            revision=settings.RAG_EVIDENCE_EMBEDDING_REVISION,
            dimension=settings.RAG_EVIDENCE_EMBEDDING_DIMENSION,
            timeout_seconds=settings.RAG_EVIDENCE_TIMEOUT_SECONDS,
        )
        rag_store = QdrantEvidenceRagStore(
            settings.RAG_EVIDENCE_QDRANT_URL,
            collection_name=settings.RAG_EVIDENCE_COLLECTION,
            dimension=settings.RAG_EVIDENCE_EMBEDDING_DIMENSION,
            timeout_seconds=settings.RAG_EVIDENCE_TIMEOUT_SECONDS,
            max_retries=settings.RAG_EVIDENCE_MAX_RETRIES,
            retry_backoff_seconds=settings.RAG_EVIDENCE_RETRY_BACKOFF_SECONDS,
        )
        rag_llm = DeepSeekEvidenceRagLlm(
            model=settings.RAG_EVIDENCE_LLM_MODEL,
            algorithm_version=settings.RAG_EVIDENCE_LLM_ALGORITHM_VERSION,
            timeout_seconds=settings.RAG_EVIDENCE_LLM_TIMEOUT_SECONDS,
        )

    def evidence_rag_permission(actor: AccountActor) -> tuple[str, str]:
        if actor.role == "enterprise_user":
            with account_uow() as uow:
                enterprise = uow.enterprises.latest_for_owner(actor.account_id)
            if enterprise is None:
                raise PermissionDenied("Enterprise account has no enterprise profile")
            return tenant_ref(enterprise.enterprise_id), (
                f"enterprise:{enterprise.enterprise_id}"
            )
        if actor.role == "personal_user":
            return personal_tenant_ref(actor.account_id), (
                f"personal:{actor.account_id}"
            )
        return PLATFORM_PUBLIC_TENANT_REF, "platform:public"

    rag_index_status_service: RagIndexStatusService | None = None
    rag_position_profile_service: RagPositionProfileService | None = None

    def rag_index_status_provider(
        payload: dict[str, object],
    ) -> dict[str, object] | None:
        nonlocal rag_index_status_service
        if rag_index_status_service is None:
            rag_index_status_service = RagIndexStatusService(
                kg_database_url=settings.KNOWLEDGE_GRAPH_RAG_DATABASE_URL,
                main_database_url=settings.DATABASE_URL,
                rag=evidence_rag,
            )
        return rag_index_status_service.status_for_payload(payload)

    def rag_profile_text_provider(
        payload: dict[str, object],
    ) -> str | None:
        nonlocal rag_position_profile_service
        if rag_position_profile_service is None:
            rag_position_profile_service = RagPositionProfileService(
                kg_database_url=settings.KNOWLEDGE_GRAPH_RAG_DATABASE_URL,
                main_database_url=settings.DATABASE_URL,
            )
        return rag_position_profile_service.profile_text(payload)

    evidence_rag = ManageEvidenceRag(
        rag_embedding,
        rag_store,
        rag_llm,
        settings.RAG_EVIDENCE_ENABLED,
        evidence_rag_permission,
        profile_text_provider=rag_profile_text_provider,
        index_status_provider=rag_index_status_provider,
        top_k=settings.RAG_EVIDENCE_TOP_K,
        max_context_chars=settings.RAG_EVIDENCE_MAX_CONTEXT_CHARS,
        multi_object_max_hits=settings.RAG_EVIDENCE_MULTI_OBJECT_MAX_HITS,
        multi_object_max_context_chars=(
            settings.RAG_EVIDENCE_MULTI_OBJECT_MAX_CONTEXT_CHARS
        ),
        min_score=settings.RAG_EVIDENCE_MIN_SCORE,
        citation_target_resolver=SqlAlchemyEvidenceCitationTargetResolver(
            database.session_factory
        ),
    )

    knowledge_graph_client = KnowledgeGraphClient(
        base_url=settings.KNOWLEDGE_GRAPH_BASE_URL,
        username=settings.KNOWLEDGE_GRAPH_SERVICE_USERNAME,
        password=settings.KNOWLEDGE_GRAPH_SERVICE_PASSWORD,
        timeout_seconds=settings.KNOWLEDGE_GRAPH_TIMEOUT_SECONDS,
    )
    tasks = ManageTasks(lambda: SqlAlchemyTaskUnitOfWork(database.session_factory))
    if settings.ENVIRONMENT == "test" and settings.TREND_INTELLIGENCE_TEST_ADAPTER_ENABLED:
        trend_intelligence_gateway = TestTrendIntelligenceGatewayV1()
    elif settings.TREND_INTELLIGENCE_ENABLED:
        trend_intelligence_gateway = HttpTrendIntelligenceGatewayV1(
            TrendIntelligenceHttpClient(
                base_url=settings.TREND_INTELLIGENCE_BASE_URL,
                token=settings.TREND_INTELLIGENCE_INTERNAL_TOKEN or "",
                timeout_seconds=settings.TREND_INTELLIGENCE_TIMEOUT_SECONDS,
                max_retries=settings.TREND_INTELLIGENCE_MAX_RETRIES,
                retry_backoff_seconds=settings.TREND_INTELLIGENCE_RETRY_BACKOFF_SECONDS,
            )
        )
    else:
        trend_intelligence_gateway = DisabledTrendIntelligenceGatewayV1()
    files = ManageFiles(
        lambda: SqlAlchemyFileUnitOfWork(database.session_factory),
        registry.file_storage,
        lambda: settings.MAX_UPLOAD_SIZE_BYTES,
    )
    system_configs = ManageSystemConfigs(
        lambda: SqlAlchemySystemConfigUnitOfWork(database.session_factory),
        secret_key=settings.JWT_SECRET_KEY,
    )
    extraction_provider = HttpJDExtractionProvider(
        settings.JD_EXTRACTION_BASE_URL,
        settings.JD_EXTRACTION_INTERNAL_TOKEN,
        settings.JD_EXTRACTION_CONNECT_TIMEOUT_SECONDS,
        settings.JD_EXTRACTION_READ_TIMEOUT_SECONDS,
        model_service_config=system_configs.resolve_runtime_model_service,
    )
    rule_extraction_provider = RuleBasedJDExtractionProvider()
    extraction_providers = {
        "llm": extraction_provider,
        "rule": rule_extraction_provider,
    }
    resumes = ManageResumes(
        lambda: SqlAlchemyResumeUnitOfWork(database.session_factory),
        files,
        IntegrationResumeInputExtractor(
            registry.file_storage, registry.document_parser, registry.ocr
        ),
        tasks,
        vector_index_enabled=settings.MATCHING_VECTOR_INDEX_ENABLED,
    )
    cv_provider = HttpCVExtractionProvider(
        settings.CV_EXTRACTION_BASE_URL,
        settings.CV_EXTRACTION_INTERNAL_TOKEN,
        settings.CV_EXTRACTION_CONNECT_TIMEOUT_SECONDS,
        settings.CV_EXTRACTION_READ_TIMEOUT_SECONDS,
        provider_name=settings.CV_EXTRACTION_PROVIDER,
        model_name=settings.CV_EXTRACTION_MODEL,
        prompt_version=settings.CV_EXTRACTION_PROMPT_VERSION,
        schema_version=settings.CV_EXTRACTION_SCHEMA_VERSION,
        normalization_version=settings.CV_EXTRACTION_NORMALIZATION_VERSION,
        taxonomy_version=settings.CV_EXTRACTION_TAXONOMY_VERSION,
        validation_policy_version=settings.CV_EXTRACTION_VALIDATION_POLICY_VERSION,
    )
    extraction_use_cases = ExtractionTaskUseCases(
        lambda: SqlAlchemyExtractionTaskUnitOfWork(
            database.session_factory,
            settings.DATA_VALIDATION_MODE,
        ),
        extraction_providers,
        settings.JD_EXTRACTION_MAX_ATTEMPTS,
        data_validation_mode=settings.DATA_VALIDATION_MODE,
    )
    extraction_worker = ExtractionTaskWorker(
        extraction_use_cases,
        poll_interval_seconds=settings.JD_EXTRACTION_WORKER_POLL_INTERVAL_SECONDS,
        concurrency=settings.JD_EXTRACTION_WORKER_CONCURRENCY,
        lease_timeout_seconds=settings.JD_EXTRACTION_WORKER_LEASE_TIMEOUT_SECONDS,
        stale_recovery_interval_seconds=(
            settings.JD_EXTRACTION_STALE_RECOVERY_INTERVAL_SECONDS
        ),
    )
    offline_importer = OfflineBundleImporter(
        OfflineImportRepository(database.session_factory),
        extraction_use_cases.import_crawler_envelope_as_jd,
        settings.ACQUISITION_EXTRACTION_MODE,
    )
    acquisition = AcquisitionUseCases(
        lambda: SqlAlchemyAcquisitionUnitOfWork(database.session_factory),
        HttpCrawlerGateway(
            base_url=settings.CRAWLER_BASE_URL,
            token=settings.CRAWLER_INTERNAL_TOKEN,
            connect_timeout_seconds=settings.CRAWLER_CONNECT_TIMEOUT_SECONDS,
            read_timeout_seconds=settings.CRAWLER_READ_TIMEOUT_SECONDS,
        ),
        LocalBundleStore(settings.ACQUISITION_BUNDLE_DIR),
        offline_importer,
        poll_interval_seconds=settings.ACQUISITION_POLL_INTERVAL_SECONDS,
        timeout_seconds=settings.ACQUISITION_TIMEOUT_SECONDS,
        stale_after_seconds=settings.ACQUISITION_STALE_AFTER_SECONDS,
    )
    cv_use_cases = CVIngestionUseCases(
        lambda: SqlAlchemyCVIngestionUnitOfWork(database.session_factory),
        cv_provider,
        ApplicationResumeImporter(resumes),
        CVValidatorSet(
            CVValidationPolicy(
                version=settings.CV_EXTRACTION_VALIDATION_POLICY_VERSION,
                provider=settings.CV_EXTRACTION_PROVIDER,
                model=settings.CV_EXTRACTION_MODEL,
                prompt_version=settings.CV_EXTRACTION_PROMPT_VERSION,
                schema_version=settings.CV_EXTRACTION_SCHEMA_VERSION,
                normalization_version=settings.CV_EXTRACTION_NORMALIZATION_VERSION,
            ),
            SqlAlchemyValidationPortFactory(
                database.session_factory,
                taxonomy_version=settings.CV_EXTRACTION_TAXONOMY_VERSION,
            ).current_catalog,
        ),
        file_input=CVFileTextExtractionAdapter(
            files,
            registry.file_storage,
            PdfTextDocumentParser(),
            DocxTextDocumentParser(),
            registry.ocr,
            lambda: settings.MAX_UPLOAD_SIZE_BYTES,
        ),
        enabled=settings.CV_EXTRACTION_ENABLED,
        max_attempts=settings.CV_EXTRACTION_MAX_ATTEMPTS,
    )
    cv_worker = CVExtractionWorker(
        cv_use_cases,
        poll_interval_seconds=settings.CV_EXTRACTION_WORKER_POLL_INTERVAL_SECONDS,
        concurrency=settings.CV_EXTRACTION_WORKER_CONCURRENCY,
        lease_timeout_seconds=settings.CV_EXTRACTION_WORKER_LEASE_TIMEOUT_SECONDS,
        stale_recovery_interval_seconds=(
            settings.CV_EXTRACTION_STALE_RECOVERY_INTERVAL_SECONDS
        ),
    )
    matching_identity = SqlAlchemyMatchingIdentityAdapter(database.session_factory)
    matching_contract_reader = KnowledgeGraphMatchingContractReader(
        SqlAlchemyMatchingContractReader(database.session_factory),
        knowledge_graph_client,
    )
    matching_service = (
        HttpMatchingServiceAdapter(
            base_url=settings.MATCHING_SERVICE_BASE_URL or "",
            issuer=settings.MATCHING_SERVICE_ISSUER,
            audience=settings.MATCHING_SERVICE_AUDIENCE,
            signing_key=settings.MATCHING_SERVICE_SIGNING_KEY or "",
            timeout_seconds=settings.MATCHING_SERVICE_TIMEOUT_SECONDS,
            max_retries=settings.MATCHING_SERVICE_MAX_RETRIES,
            retry_backoff_seconds=settings.MATCHING_SERVICE_RETRY_BACKOFF_SECONDS,
        )
        if settings.MATCHING_SERVICE_ENABLED
        else DisabledMatchingServiceAdapter()
    )
    return ApplicationContainer(
        accounts=AccountHandlers(
            registration=RegisterAccount(
                account_uow,
                passwords,
                allow_demo_admin_registration=(
                    settings.ALLOW_DEMO_ADMIN_REGISTRATION
                ),
            ),
            authentication=AuthenticateAccount(account_uow, passwords, tokens),
            password=ChangePassword(account_uow, passwords, tokens),
            management=ManageAccount(account_uow),
            enterprises=ManageEnterprise(account_uow),
        ),
        recruitment=RecruitmentHandlers(
            jobs=ManageRecruitmentJobs(
                lambda: SqlAlchemyRecruitmentUnitOfWork(database.session_factory),
                vector_index_enabled=settings.MATCHING_VECTOR_INDEX_ENABLED,
            ),
            published_jobs=BrowsePublishedJobs(
                lambda: SqlAlchemyRecruitmentUnitOfWork(database.session_factory)
            ),
        ),
        jds=JDUseCases(
            jd_uow,
            OpenPyxlJDExporter(),
            VersionedJDSchemaAdapter(),
            data_validation_mode=settings.DATA_VALIDATION_MODE,
            extraction_providers=extraction_providers,
        ),
        discovery=PositionDiscoveryHandlers(
            start=StartPositionDiscovery(
                discovery_uow, discovery_gateway or HttpEmergingDiscoveryGateway()
            ),
            query=QueryPositionDiscovery(
                discovery_uow,
                experiment_report_loader=formal_discovery_experiment_report,
                experiment_clusters_loader=formal_discovery_experiment_clusters,
                experiment_replay_loader=lambda: (
                    knowledge_graph_client.replay_formal_emergence_v32().data or {}
                ),
            ),
            delete=DeletePositionCluster(discovery_uow),
        ),
        discovery_candidates=DiscoveryCandidateHandlers(
            query=QueryDiscoveryCandidates(
                gateway=HttpEmergingDiscoveryGateway()
            )
        ),
        emerging_positions=EmergingPositionHandlers(
            create=CreateEmergingCandidate(emerging_uow),
            query=QueryEmergingCandidates(emerging_uow),
            import_formal=ImportFormalExperimentResults(
                emerging_uow,
                formal_discovery_experiment_clusters,
            ),
            update=UpdateEmergingCandidate(emerging_uow),
            submit_review=SubmitEmergingDefinition(emerging_uow),
            review=ReviewEmergingDefinition(emerging_uow),
            delete=DeleteEmergingCandidate(emerging_uow),
            publish=PublishEmergingCandidate(emerging_uow),
            promote=PromoteEmergingCandidate(emerging_uow),
            assessment=QueryGerminationAssessment(emerging_uow),
            generate_definition=GenerateEmergingDefinition(
                emerging_uow,
                formal_discovery_experiment_clusters,
            ),
            versions=QueryDefinitionVersions(emerging_uow),
            select_version=SelectDefinitionVersion(emerging_uow),
        ),
        governance=GovernanceHandlers(
            evidence=ManageEvidence(
                lambda: SqlAlchemyGovernanceUnitOfWork(database.session_factory)
            ),
            reviews=ManageReviews(
                lambda: SqlAlchemyGovernanceUnitOfWork(database.session_factory)
            ),
            rag=ManageRag(
                lambda: SqlAlchemyGovernanceUnitOfWork(database.session_factory),
                EvidenceRetrieverAdapter(registry.evidence_retriever),
            ),
        ),
        system=QuerySystemStatus(
            SqlAlchemySystemStatusAdapter(database, settings, registry)
        ),
        system_configs=system_configs,
        predictions=ManagePredictedPositions(
            lambda: SqlAlchemyTrendUnitOfWork(database.session_factory),
            tasks,
            trend_intelligence_gateway,
            algorithm_version=settings.TREND_INTELLIGENCE_ALGORITHM_VERSION,
            formula_version=settings.TREND_INTELLIGENCE_FORMULA_VERSION,
            publication_min_source_coverage=settings.TREND_PUBLICATION_MIN_SOURCE_COVERAGE,
            publication_high_risk_flags=tuple(
                item.strip()
                for item in settings.TREND_PUBLICATION_HIGH_RISK_FLAGS.split(",")
                if item.strip()
            ),
        ),
        files=files,
        tasks=tasks,
        matching=ManageMatching(
            lambda: SqlAlchemyMatchingUnitOfWork(database.session_factory),
            SqlAlchemyResumeProfileAdapter(database.session_factory),
            KnowledgeGraphPositionProfileAdapter(knowledge_graph_client),
            matching_service,
            matching_identity,
            tasks,
            matching_contract_reader,
            SqlAlchemyMatchingPositionCatalog(database.session_factory),
        ),
        matching_scenarios=ManageMatchingScenarios(
            lambda: SqlAlchemyWhatIfScenarioUnitOfWork(
                database.session_factory
            )
        ),
        evidence_read=SqlAlchemyEvidenceReadAdapter(
            database.session_factory
        ),
        review_chain=SqlAlchemyReviewChainAdapter(database.session_factory),
        learning_paths=ManageLearningPaths(
            lambda: SqlAlchemyMatchingUnitOfWork(database.session_factory),
            SqlAlchemyResumeProfileAdapter(database.session_factory),
            KnowledgeGraphPositionProfileAdapter(knowledge_graph_client),
            matching_service,
            matching_identity,
            matching_contract_reader,
        ),
        resumes=resumes,
        skills=ManageSkills(
            lambda: SqlAlchemySkillUnitOfWork(database.session_factory),
            normalization_embedding=normalization_embedding,
        ),
        positions=ManagePositions(
            lambda: SqlAlchemyPositionUnitOfWork(database.session_factory),
            vector_index_enabled=settings.MATCHING_VECTOR_INDEX_ENABLED,
        ),
        evaluation=ManageEvaluation(
            lambda: SqlAlchemyEvaluationUnitOfWork(database.session_factory), tasks
        ),
        trend_reports=ManageTrendReports(
            lambda: SqlAlchemyTrendAnalysisUnitOfWork(
                database.session_factory, knowledge_graph_client
            ),
            tasks,
            trend_intelligence_gateway,
            algorithm_version=settings.TREND_SKILL_ALGORITHM_VERSION,
            formula_version=settings.TREND_SKILL_FORMULA_VERSION,
            config_version=settings.TREND_SKILL_CONFIG_VERSION,
            publication_min_source_coverage=settings.TREND_PUBLICATION_MIN_SOURCE_COVERAGE,
            publication_high_risk_flags=tuple(
                item.strip() for item in settings.TREND_PUBLICATION_HIGH_RISK_FLAGS.split(",")
                if item.strip()
            ),
        ),
        feedback=ManageFeedback(
            lambda: SqlAlchemyFeedbackUnitOfWork(database.session_factory)
        ),
        ocr=ManageOCR(
            lambda: SqlAlchemyOCRUnitOfWork(database.session_factory),
            IntegrationOCRExtractor(registry.ocr), tasks,
        ),
        embeddings=ManageEmbeddings(
            {
                "jd": SqlAlchemyJDEmbeddingSource(database.session_factory),
                "resume": SqlAlchemyResumeEmbeddingSource(database.session_factory),
                "skill": SqlAlchemySkillEmbeddingSource(database.session_factory),
                "position": SqlAlchemyPositionEmbeddingSource(database.session_factory),
                "evidence": SqlAlchemyEvidenceEmbeddingSource(database.session_factory),
                "relation": SqlAlchemyRelationEmbeddingSource(database.session_factory),
            },
            IntegrationEmbeddingGateway(registry.embedding),
            IntegrationVectorGateway(registry.vector_store),
            tasks,
        ),
        evidence_rag=evidence_rag,
        candidates=ManageCandidates(
            lambda: SqlAlchemyCandidateUnitOfWork(database.session_factory),
            SqlAlchemyCandidateJobProfileAdapter(database.session_factory),
            SqlAlchemyCandidateResumeProfileAdapter(database.session_factory),
            tasks,
            matching_service=matching_service,
            matching_identities=matching_identity,
            contracts=matching_contract_reader,
            matching_uow_factory=lambda: SqlAlchemyMatchingUnitOfWork(database.session_factory),
            vector_index_enabled=settings.MATCHING_VECTOR_INDEX_ENABLED,
        ),
        knowledge_graph=ManageKnowledgeGraphIntegration(
            KnowledgeGraphAdapterFactory(
                database.session_factory,
                knowledge_graph_client,
                enabled=settings.KNOWLEDGE_GRAPH_ENABLED,
            )
        ),
        acquisition=acquisition,
        source_jds=SourceJDUseCases(
            lambda: SqlAlchemySourceJDUnitOfWork(database.session_factory)
        ),
        extraction_tasks=extraction_use_cases,
        extraction_worker=RunPendingExtractionTasks(extraction_worker),
        outbox_events=ManageOutboxEvents(
            lambda: SqlAlchemyOutboxOperationsUnitOfWork(
                database.session_factory
            )
        ),
        cv_ingestion=cv_use_cases,
        cv_extraction_worker=RunPendingCVExtractionTasks(cv_worker),
        integration_status=QueryIntegrationStatus(
            lambda: SqlAlchemyIntegrationStatusReader(database.session_factory)
        ),
        matching_contracts=MatchingContractService(
            matching_contract_reader
        ),
    )
