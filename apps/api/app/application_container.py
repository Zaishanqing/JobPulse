from __future__ import annotations

from dataclasses import dataclass

from app.contexts.access import AccountHandlers
from app.contexts.talent_acquisition import ManageCandidates
from app.contexts.platform import ManageEmbeddings
from app.contexts.evidence_rag import ManageEvidenceRag
from app.contexts.evaluation import ManageEvaluation
from app.contexts.governance_feedback import ManageFeedback
from app.contexts.platform import ManageFiles
from app.contexts.governance_feedback import GovernanceHandlers
from app.contexts.jd_lifecycle import JDUseCases
from app.contexts.knowledge_graph import ManageKnowledgeGraphIntegration
from app.contexts.matching_learning import ManageLearningPaths, ManageMatching
from app.contexts.platform import ManageOCR
from app.contexts.catalog import ManagePositions
from app.contexts.talent_acquisition import RecruitmentHandlers
from app.contexts.talent_acquisition import ManageResumes
from app.contexts.catalog import ManageSkills
from app.contexts.platform import (
    ManageOutboxEvents,
    ManageSystemConfigs,
    QuerySystemStatus,
)
from app.contexts.market_intelligence import ManageTrendReports
from app.contexts.market_intelligence import ManagePredictedPositions
from app.contexts.discovery import DiscoveryCandidateHandlers, PositionDiscoveryHandlers
from app.contexts.emerging_positions import EmergingPositionHandlers
from app.contexts.tasks import ManageTasks
from app.contexts.acquisition import AcquisitionUseCases
from app.contexts.source_jds import SourceJDUseCases
from app.contexts.extraction_tasks import ExtractionTaskUseCases, RunPendingExtractionTasks
from app.contexts.cv_ingestion import CVIngestionUseCases, RunPendingCVExtractionTasks
from app.contexts.integration_status import QueryIntegrationStatus
from app.contexts.matching_learning.contracts_service import MatchingContractService
from app.contexts.insight_cards.matching_scenarios import (
    ManageMatchingScenarios,
)
from app.contexts.insight_cards.evidence_resolver import EvidenceReadPort
from app.contexts.insight_cards.review_chain import ReviewChainPort


@dataclass(frozen=True)
class ApplicationContainer:
    """API-visible graph containing application handlers only."""

    accounts: AccountHandlers
    recruitment: RecruitmentHandlers
    jds: JDUseCases
    discovery: PositionDiscoveryHandlers
    discovery_candidates: DiscoveryCandidateHandlers
    emerging_positions: EmergingPositionHandlers
    governance: GovernanceHandlers
    system: QuerySystemStatus
    system_configs: ManageSystemConfigs
    predictions: ManagePredictedPositions
    files: ManageFiles
    tasks: ManageTasks
    matching: ManageMatching
    matching_scenarios: ManageMatchingScenarios
    evidence_read: EvidenceReadPort
    review_chain: ReviewChainPort
    learning_paths: ManageLearningPaths
    resumes: ManageResumes
    skills: ManageSkills
    positions: ManagePositions
    evaluation: ManageEvaluation
    trend_reports: ManageTrendReports
    feedback: ManageFeedback
    ocr: ManageOCR
    embeddings: ManageEmbeddings
    evidence_rag: ManageEvidenceRag
    candidates: ManageCandidates
    knowledge_graph: ManageKnowledgeGraphIntegration
    source_jds: SourceJDUseCases
    extraction_tasks: ExtractionTaskUseCases
    extraction_worker: RunPendingExtractionTasks
    outbox_events: ManageOutboxEvents
    cv_ingestion: CVIngestionUseCases
    cv_extraction_worker: RunPendingCVExtractionTasks
    integration_status: QueryIntegrationStatus
    matching_contracts: MatchingContractService | None = None
    acquisition: AcquisitionUseCases | None = None
