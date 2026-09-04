from datetime import date, datetime

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmergenceV32MemberInput(StrictModel):
    document_id: str = Field(min_length=1)
    source_record_id: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    observation_date: date
    date_source: Literal["publish_date", "crawl_date"]
    company: str | None = None
    source_platform: str | None = None
    bundle_id: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class EmergenceV32ClusterInput(StrictModel):
    cluster_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    skills: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    members: list[EmergenceV32MemberInput] = Field(min_length=1)


class EmergenceV32EvaluateInput(StrictModel):
    dataset_id: str = Field(min_length=1)
    clusters: list[EmergenceV32ClusterInput] = Field(min_length=1)


class PublishedJDFactV3Result(BaseModel):
    contract_version: Literal["published-jd-fact.v3"]
    document_id: str
    source_fact_id: str
    source_fact_version: str
    source_version: str
    idempotent: bool
    stale: bool


class PublishedJDFactV3Envelope(BaseModel):
    code: Literal[0]
    message: str
    data: PublishedJDFactV3Result
    trace_id: str


class ErrorEnvelope(BaseModel):
    code: int
    message: str
    data: Any | None = None
    trace_id: str | None = None


class JDCreate(BaseModel):
    document_id: str | None = None
    raw_text: str
    source_type: str = "manual"
    source_name: str | None = None
    enterprise_name: str | None = None
    published_at: datetime | None = None
    source_credibility: float = Field(1, ge=0, le=1)
    is_synthetic: bool = False


class QualityInput(BaseModel):
    algorithm_config: dict | None = None


class BuildInput(BaseModel):
    window_start: datetime | None = None
    window_end: datetime | None = None
    minimum_effective_weight: float = .05
    minimum_valid_samples: int = Field(1, ge=1)


class BuildSummaryDTO(StrictModel):
    input: dict[str, int]
    valid: dict[str, int]
    deduplication: dict[str, int]
    excluded: dict[str, Any]
    risks: dict[str, Any]
    manual_modifications: dict[str, int]
    included_samples: int
    excluded_samples: int
    relations: int
    minimum_valid_samples: int


class BuildGraphResultDTO(StrictModel):
    build_run_id: int
    status: str
    summary: BuildSummaryDTO


class BuildGraphEnvelope(StrictModel):
    code: Literal[0]
    message: str
    data: BuildGraphResultDTO
    trace_id: str


class RelationStatisticsDTO(StrictModel):
    supporting_jd_count: int
    deduplicated_jd_count: int
    enterprise_count: int
    source_count: int
    evidence_count: int
    first_seen_at: str | None
    last_seen_at: str | None
    raw_frequency: float
    quality_adjusted_frequency: float


class RelationItemDTO(BaseModel):
    model_config = ConfigDict(extra="allow")
    relation_id: int
    skill_id: str
    statistics: RelationStatisticsDTO


class RelationPageDTO(StrictModel):
    position_id: str
    version_id: int | None
    is_current: bool
    page: int
    page_size: int
    total: int
    items: list[RelationItemDTO]


class RelationPageEnvelope(StrictModel):
    code: Literal[0]
    message: str
    data: RelationPageDTO
    trace_id: str


class RelationExplanationDTO(BaseModel):
    model_config = ConfigDict(extra="allow")
    relation_id: int
    position_id: str
    skill_id: str
    statistics: dict[str, Any]
    sources: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    weight_basis: dict[str, Any]
    confidence_basis: dict[str, Any]
    quality_impact: dict[str, Any]
    manual_modification_history: list[dict[str, Any]]
    version_id: int | None
    is_current: bool


class RelationExplanationEnvelope(StrictModel):
    code: Literal[0]
    message: str
    data: RelationExplanationDTO
    trace_id: str


class ReviewAction(BaseModel):
    reason: str = Field(min_length=1)
    payload: dict | None = None

    @field_validator("reason")
    @classmethod
    def reason_must_be_meaningful(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise PydanticCustomError("blank_reason", "reason must not be blank")
        return value


class ReviewModifyPayload(BaseModel):
    model_config = ConfigDict(extra="allow")
    expected_revision: int | None = Field(None, ge=1)
    weight: float | None = Field(None, ge=0, le=1)
    confidence: float | None = Field(None, ge=0, le=1)
    importance_level: Literal["core", "important", "supplementary"] | None = None


class ReviewModifyAction(ReviewAction):
    payload: ReviewModifyPayload | None = None


class ReviewTaskItemDTO(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int
    object_type: str
    object_id: str
    build_run_id: int | None
    status: str
    assignee_id: int | None
    original_values: dict[str, Any]
    current_values: dict[str, Any]
    modified_values: dict[str, Any]
    evidence: list[dict[str, Any]]
    impacted_relations: list[dict[str, Any]]
    risk_level: Literal["low", "medium", "high"]
    history: list[dict[str, Any]]
    allowed_actions: list[str]


class ReviewTaskListEnvelope(StrictModel):
    code: Literal[0]
    message: str
    data: list[ReviewTaskItemDTO]
    trace_id: str


class ReviewActionResultDTO(StrictModel):
    id: int
    status: str
    action: Literal["claim", "modify", "approve", "reject"]
    feedback: str
    assignee_id: int | None
    allowed_actions: list[str]


class ReviewActionEnvelope(StrictModel):
    code: Literal[0]
    message: str
    data: ReviewActionResultDTO
    trace_id: str


class PublishInput(BaseModel):
    reason: str | None = None
    version_name: str | None = Field(None, min_length=1, max_length=80)
    version_number: int | None = Field(None, ge=1)
    release_notes: str | None = None


class RelationModify(BaseModel):
    build_run_id: int = Field(ge=1)
    position_id: str = Field(min_length=1)
    expected_revision: int = Field(ge=1)
    weight: float | None = Field(None, ge=0, le=1)
    confidence: float | None = Field(None, ge=0, le=1)
    importance_level: Literal["core", "important", "supplementary"] | None = None
    reason: str = Field(min_length=1)

    @field_validator("reason")
    @classmethod
    def reason_must_be_meaningful(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise PydanticCustomError("blank_reason", "reason must not be blank")
        return value


class DraftCreate(BaseModel):
    base_version_id: int | None = Field(None, ge=1)


class LoginInput(BaseModel):
    username: str
    password: str


class ReviewTaskCreate(BaseModel):
    object_type: str
    object_id: str
    build_run_id: int | None = None
    payload: dict = Field(default_factory=dict)


class AutoReviewBuildInput(BaseModel):
    policy_version: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1)


class AlgorithmConfigInput(BaseModel):
    version: str
    payload: dict
    active: bool = True


class MappingSignalsInput(StrictModel):
    uncertainty: float = Field(ge=0, le=1)
    graph_impact: float = Field(ge=0, le=1)
    frequency: float = Field(ge=0, le=1)
    source_diversity: float = Field(ge=0, le=1)
    drift: float = Field(ge=0, le=1)


class MappingWeightsInput(StrictModel):
    uncertainty: float = Field(ge=0)
    graph_impact: float = Field(ge=0)
    frequency: float = Field(ge=0)
    source_diversity: float = Field(ge=0)
    drift: float = Field(ge=0)


class MappingAffectedContextInput(StrictModel):
    source_fact_id: str = Field(min_length=1)
    requirement_id: str = Field(min_length=1)


class MappingCandidateInput(StrictModel):
    candidate_id: str = Field(min_length=1, max_length=80)
    source_expression: str = Field(min_length=1, max_length=300)
    proposed_skill_id: str = Field(min_length=1)
    signals: MappingSignalsInput
    weights: MappingWeightsInput
    model_version: str = Field(min_length=1)
    index_version: str = Field(min_length=1)
    mapping_policy_version: str = Field(min_length=1)
    affected_contexts: list[MappingAffectedContextInput] = Field(min_length=1)


class MappingReviewInput(StrictModel):
    expected_revision: int = Field(ge=1)
    decision: Literal["accept", "reject", "no_match", "supersede"]
    reason: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    effective_scope: str = Field(min_length=1)
    replacement_candidate_id: str | None = None


class DependencyReferenceInput(StrictModel):
    consumer_system: Literal["matching", "trend", "discovery"]
    reference_type: str = Field(min_length=1, max_length=80)
    reference_id: str = Field(min_length=1, max_length=120)
    graph_version_id: int = Field(ge=1)
    metadata: dict = Field(default_factory=dict)


class DependencyPolicyInput(StrictModel):
    minimum_joint_support: int = Field(ge=1)
    minimum_conditional_probability: float = Field(gt=0, le=1)
    minimum_source_diversity: int = Field(ge=1)
    minimum_enterprise_diversity: int = Field(ge=1)
    maximum_enterprise_share: float = Field(gt=0, le=1)
    bootstrap_iterations: int = Field(ge=100)
    confidence_level: float = Field(gt=0, lt=1)
    minimum_stable_slices: int = Field(ge=1)


class DependencyReviewInput(StrictModel):
    decision: Literal["accept", "reject"]
    reason: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)


class ProjectionRebuildInput(StrictModel):
    projection_version: str = Field(min_length=1, max_length=100)


class WatermarkComparisonInput(StrictModel):
    left_build_run_id: int = Field(ge=1)
    right_build_run_id: int = Field(ge=1)
    approved_catalog_crosswalk: bool
    policy_replay_completed: bool
    minimum_input_coverage: float = Field(ge=0, le=1)


class PositionProfileBatchInput(StrictModel):
    position_ids: list[str] = Field(min_length=1, max_length=500)
    contract_version: Literal["position-profile.v3"] = "position-profile.v3"
    graph_version_ids: dict[str, int] = Field(default_factory=dict)
    view: Literal["published", "draft", "experimental"] = "published"
    draft_ids: dict[str, int] = Field(default_factory=dict)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=100, ge=1, le=100)


class SkillRelationBatchInput(StrictModel):
    skill_ids: list[str] = Field(min_length=1, max_length=500)
