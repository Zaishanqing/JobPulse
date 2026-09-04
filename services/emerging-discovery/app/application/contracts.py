"""Commands and results owned by the discovery application layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from app.domain.discovery import (
    ClusterFeatureSummary,
    GeneratedDefinition,
    GeneratedSkill,
    JDSnapshot,
    PositionReference,
)
from app.domain.lineage import LineageRelation
from app.domain.values import FrozenDict, JsonObject


class DiscoveryContractConflict(ValueError):
    """Raised when a request_id is reused with a different payload."""


@dataclass(frozen=True)
class SimilarityThreshold:
    value: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.value <= 1.0:
            raise ValueError("similarity threshold must be between zero and one")


@dataclass(frozen=True)
class AlgorithmSelection:
    canonical_name: str
    requested_name: str
    similarity_threshold: SimilarityThreshold


@dataclass(frozen=True)
class HistoricalTimeWindow:
    window_id: str
    start: date
    end: date


@dataclass(frozen=True)
class DiscoveryTimeWindow:
    start: date | None = None
    end: date | None = None
    windows: tuple[HistoricalTimeWindow, ...] = field(default_factory=tuple)
    current_observation_window_id: str | None = None

    @property
    def current_observation_window(self) -> HistoricalTimeWindow:
        window_id = self.current_observation_window_id
        if window_id is None and self.windows:
            return self.windows[-1]
        for window in self.windows:
            if window.window_id == window_id:
                return window
        raise ValueError("current observation window must be declared in historical windows")


@dataclass(frozen=True)
class DiscoveryConfig:
    values: JsonObject = field(default_factory=FrozenDict)


@dataclass(frozen=True)
class ComparisonClusterResult:
    cluster_key: str
    member_jd_ids: tuple[str, ...]
    member_fact_ids: tuple[str, ...]


@dataclass(frozen=True)
class AlgorithmEvaluationResult:
    algorithm: str
    feature_name: str
    clustering_name: str
    parameters: JsonObject
    cluster_count: int
    noise_ratio: float
    silhouette_coefficient: float | None
    intra_cluster_similarity: float | None
    inter_cluster_difference: float | None
    runtime_ms: float
    clusters: tuple[ComparisonClusterResult, ...]
    noise_points: tuple[JsonObject, ...]
    enterprise_debias: JsonObject = field(default_factory=FrozenDict)
    stability_analysis: JsonObject = field(default_factory=FrozenDict)
    parameter_sensitivity: tuple[JsonObject, ...] = field(default_factory=tuple)
    recommendation_score: float = 0.0


@dataclass(frozen=True)
class AlgorithmComparisonResult:
    contract_version: str
    request_id: str
    input_quality_report: JsonObject
    algorithms: tuple[AlgorithmEvaluationResult, ...]
    recommended_algorithm: str
    recommendation_reason: str


@dataclass(frozen=True)
class RunDiscoveryCommand:
    contract_version: str
    request_id: str
    algorithm: str
    snapshots: tuple[JDSnapshot, ...]
    position_references: tuple[PositionReference, ...] = field(default_factory=tuple)
    config: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    time_window: DiscoveryTimeWindow = field(default_factory=DiscoveryTimeWindow)

    @property
    def time_window_start(self) -> date | None:
        return self.time_window.start

    @property
    def time_window_end(self) -> date | None:
        return self.time_window.end


@dataclass(frozen=True)
class DiscoveryClusterResult:
    cluster_id: str
    cluster_name: str
    sample_count: int
    core_skills: tuple[GeneratedSkill, ...]
    representative_titles: tuple[str, ...]
    representative_jd_ids: tuple[str, ...]
    representative_members: tuple[JsonObject, ...]
    core_responsibilities: tuple[str, ...]
    semantic_centroid: tuple[float, ...]
    algorithm_sources: tuple[str, ...]
    merge_basis: JsonObject
    stability_score: float
    growth_score: float
    distance_from_existing_positions: float
    feature_summary: ClusterFeatureSummary
    germination_assessment: "DiscoveryAssessmentResult"
    generated_definition: GeneratedDefinition


@dataclass(frozen=True)
class DiscoveryAssessmentResult:
    germination_score: float
    score_dimensions: FrozenDict[str, float]
    level: str
    qualified_as_emerging: bool
    decision_reason: str
    evidence_package: JsonObject


@dataclass(frozen=True)
class DiscoveryLineageResult:
    relation: LineageRelation


@dataclass(frozen=True)
class DiscoveryResult:
    contract_version: str
    run_id: str
    request_id: str
    status: str
    algorithm_version: str
    formula_version: str
    created_at: datetime
    completed_at: datetime
    clusters: tuple[DiscoveryClusterResult, ...]
    lineages: tuple[DiscoveryLineageResult, ...]
    input_quality_report: JsonObject = field(default_factory=FrozenDict)
    run_context: JsonObject = field(default_factory=FrozenDict)
    payload_fingerprint: str = ""
