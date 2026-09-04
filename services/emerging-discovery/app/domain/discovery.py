"""Discovery domain values owned by the domain layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.domain.germination import GerminationAssessmentResult
from app.domain.values import FrozenDict, JsonObject


@dataclass(frozen=True)
class SkillReference:
    raw_skill: str | None = None
    normalized_skill_id: str | None = None

    @property
    def identity(self) -> str | None:
        return self.normalized_skill_id or self.raw_skill


@dataclass(frozen=True)
class JDStructuredData:
    responsibilities: tuple[str, ...]
    required_skills: tuple[SkillReference, ...]
    bonus_skills: tuple[SkillReference, ...]
    business_scenarios: tuple[str, ...]
    position_title: str | None = None
    industry: str | None = None
    extensions: JsonObject = field(default_factory=FrozenDict)


@dataclass(frozen=True)
class JDSnapshot:
    jd_id: str
    schema_version: str
    review_status: str
    title: str
    source_name: str | None
    publish_date: date | None
    structured_data: JDStructuredData
    source_fact_id: str
    source_fact_version: str
    window_id: str = ""
    consumption_path: str | None = None
    content_hash: str | None = None
    source_record_id: str | None = None
    bundle_id: str | None = None
    date_source: str | None = None


@dataclass(frozen=True)
class PositionReference:
    position_id: str
    required_skills: tuple[SkillReference, ...] = field(default_factory=tuple)
    graph_version_id: str = "unavailable"


@dataclass(frozen=True)
class AlgorithmMetadata:
    algorithm_name: str
    requested_algorithm: str
    algorithm_version: str
    feature_version: str
    similarity_threshold: float
    random_seed: int
    extensions: JsonObject = field(default_factory=FrozenDict)


@dataclass(frozen=True)
class GeneratedSkill:
    raw_skill: str
    normalized_skill_id: str
    confidence: float


@dataclass(frozen=True)
class GeneratedDefinition:
    position_name: str
    core_responsibilities: tuple[str, ...]
    required_skills: tuple[GeneratedSkill, ...]
    bonus_skills: tuple[GeneratedSkill, ...]
    industry_scenarios: tuple[str, ...]
    generation_mode: str
    field_evidence: JsonObject = field(default_factory=FrozenDict)
    position_summary: str = ""
    distinguishing_features: tuple[str, ...] = field(default_factory=tuple)
    representative_enterprises: JsonObject = field(default_factory=FrozenDict)
    growth_trajectory: tuple[JsonObject, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AlgorithmCluster:
    key: str
    cluster_name: str
    members: tuple[JDSnapshot, ...]
    core_skills: tuple[str, ...]
    stability_score: float
    centroid: tuple[float, ...]
    algorithm_version: str
    similarity_threshold: float
    random_seed: int
    core_responsibilities: tuple[str, ...] = field(default_factory=tuple)
    semantic_centroid: tuple[float, ...] = field(default_factory=tuple)
    algorithm_sources: tuple[str, ...] = field(default_factory=tuple)
    merge_basis: JsonObject = field(default_factory=FrozenDict)
    assessment: GerminationAssessmentResult | None = None
    generated_definition: GeneratedDefinition | None = None


@dataclass(frozen=True)
class EmbeddingVector:
    jd_id: str
    values: tuple[float, ...]


@dataclass(frozen=True)
class AlgorithmOutput:
    algorithm_version: str
    formula_version: str
    metadata: AlgorithmMetadata
    embeddings: tuple[EmbeddingVector, ...]
    clusters: tuple[AlgorithmCluster, ...]

    def embedding_for(self, jd_id: str) -> tuple[float, ...]:
        return next(item.values for item in self.embeddings if item.jd_id == jd_id)


@dataclass(frozen=True)
class ClusterFeatureSummary:
    metadata: AlgorithmMetadata
    centroid: tuple[float, ...]
