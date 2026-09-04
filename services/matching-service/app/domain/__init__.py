from app.domain.evaluation import MatchEvaluation
from app.domain.gap_analysis import GapAnalysisConfig, build_gap_analysis
from app.domain.gaps import GapAnalysis
from app.domain.integration import (
    ContractIntegrationResult,
    ContractIssue,
    SourceVersionRecord,
)
from app.domain.matching import MatchingAlgorithmConfig, build_match_evaluation
from app.domain.profiles import CVMatchProfile, PositionMatchProfile
from app.domain.scoring import ScoringConfig, ScoringWeights, score_match_evaluation
from app.domain.skill_relations import SkillRelation
from app.domain.vector_indexing import (
    VectorIndexReferenceRecord,
    VectorOutboxEvent,
    VectorOutboxPayload,
)

__all__ = [
    "CVMatchProfile",
    "ContextMatchingConfig",
    "GapAnalysis",
    "GapAnalysisConfig",
    "ContractIntegrationResult",
    "ContractIssue",
    "MatchEvaluation",
    "MatchingAlgorithmConfig",
    "PositionMatchProfile",
    "ScoringConfig",
    "ScoringWeights",
    "SkillRelation",
    "SourceVersionRecord",
    "VectorIndexReferenceRecord",
    "VectorOutboxEvent",
    "VectorOutboxPayload",
    "build_match_evaluation",
    "build_gap_analysis",
    "score_match_evaluation",
]
from app.domain.context_matching import ContextMatchingConfig
