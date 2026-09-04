"""Immutable output contracts for deterministic and semantic matching."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from app.domain.deepseek_candidates import SkillSemanticCandidate
from app.domain.profiles import (
    CapabilityVerificationStatus,
    DemonstratedLevel,
    Evidence,
    ImmutableDTO,
)
from app.domain.semantic_retrieval import (
    SemanticCandidate,
    SemanticMatchExplanation,
    SemanticRetrievalEvidence,
)

HardConstraintStatus = Literal[
    "pass", "partial", "fail", "unknown", "unresolved", "not_required"
]
SkillMatchStatus = Literal[
    "matched", "partial", "weak", "declared_only", "missing", "unknown", "unresolved"
]
ContextMatchStatus = Literal[
    "matched", "partial", "not_observed", "unknown", "unresolved"
]
EvaluationStatus = Literal["completed", "rejected"]
ScoreDimension = Literal[
    "required_skills",
    "responsibilities",
    "projects",
    "capability_level",
    "hard_conditions",
    "business_scenarios",
    "bonus_transferable",
    "requirement_groups",
    "semantic",
]
DimensionStatus = Literal["scored", "uncertain", "missing_evaluation", "not_applicable"]


class RequirementGroupResult(ImmutableDTO):
    """Explainable result for one Requirement Graph operator."""

    group_id: str = Field(min_length=1)
    group_type: Literal["must", "should", "and", "or", "one_of", "min_count"]
    priority: Literal["required", "preferred", "bonus", "unknown"]
    status: Literal["satisfied", "partial", "unsatisfied", "unknown", "unresolved"]
    required_count: int = Field(ge=0)
    satisfied_count: int = Field(ge=0)
    evaluable_count: int = Field(ge=0)
    child_result_ids: tuple[str, ...] = ()
    covered_result_ids: tuple[str, ...] = ()
    covered_dimensions: tuple[ScoreDimension, ...] = ()
    is_root: bool = False
    score: float | None = Field(default=None, ge=0, le=1)
    reason_code: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    position_evidence: tuple[Evidence, ...] = ()

class HardConstraintResult(ImmutableDTO):
    requirement_id: str = Field(min_length=1)
    constraint_type: Literal[
        "education",
        "experience",
        "certificate",
        "language",
        "location",
        "availability",
    ]
    status: HardConstraintStatus
    required_value: str | None
    candidate_value: str | None
    position_evidence: tuple[Evidence, ...] = ()
    candidate_evidence: tuple[Evidence, ...] = ()
    reason_code: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class SkillResult(ImmutableDTO):
    requirement_id: str = Field(min_length=1)
    skill_id: str | None
    skill_name: str | None
    importance_level: Literal["required", "bonus"]
    requirement_weight: float = Field(default=1.0, ge=0, le=1)
    required_level: str | None
    candidate_declared_level: str | None
    candidate_demonstrated_level: DemonstratedLevel | None
    verification_status: CapabilityVerificationStatus | None
    match_status: SkillMatchStatus
    position_evidence: tuple[Evidence, ...] = ()
    candidate_evidence: tuple[Evidence, ...] = ()
    reason_code: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    match_type: Literal[
        "exact",
        "equivalent",
        "parent_child",
        "prerequisite",
        "related",
        "transferable",
        "semantic_candidate",
        "semantic_text",
        "none",
    ] = "none"
    related_candidate_skill_id: str | None = None
    prerequisite_skill_ids: tuple[str, ...] = ()
    relation_type: Literal[
        "equivalent",
        "parent_child",
        "prerequisite",
        "related",
        "transferable",
        "unknown",
    ] | None = None
    relation_confidence: float | None = Field(default=None, ge=0, le=1)
    relation_evidence: tuple[Evidence, ...] = ()
    relation_source: str | None = None
    relation_graph_version: str | None = None
    transferability_score: float = Field(default=0.0, ge=0, le=1)
    semantic_model: str | None = None
    semantic_algorithm_version: str | None = None
    semantic_candidate_id: str | None = None
    candidate_ownership: str | None = None
    required_ownership: str | None = None
    skill_present: bool = False
    proficiency_satisfied: bool | None = None
    ownership_satisfied: bool | None = None
    evidence_sufficient: bool = False
    semantic_evidence_link_ids: tuple[str, ...] = ()


class ResponsibilityCandidate(ImmutableDTO):
    """One retrieved candidate for a JD responsibility with full audit trail."""

    experience_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    retrieval_score: float = Field(ge=-1, le=1)
    ce_score: float | None = Field(default=None)
    threshold_margin: float | None = Field(default=None)
    evidence_refs: tuple[Evidence, ...] = ()


class EvaluationSummary(ImmutableDTO):
    hard_constraint_pass_count: int = Field(ge=0)
    hard_constraint_fail_count: int = Field(ge=0)
    required_skill_matched_count: int = Field(ge=0)
    required_skill_missing_count: int = Field(ge=0)
    bonus_skill_matched_count: int = Field(ge=0)
    bonus_skill_missing_count: int = Field(ge=0)
    coverage_denominator_policy: Literal[
        "exclude_unknown_unresolved_and_not_required"
    ] = "exclude_unknown_unresolved_and_not_required"


class ResponsibilityResult(ImmutableDTO):
    requirement_id: str = Field(min_length=1)
    position_requirement: str = Field(min_length=1)
    candidate_experience_id: str | None
    candidate_experience: str | None
    match_status: ContextMatchStatus
    matching_rules: tuple[str, ...] = ()
    position_evidence: tuple[Evidence, ...] = ()
    candidate_evidence: tuple[Evidence, ...] = ()
    reason_code: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    match_type: Literal["deterministic", "semantic", "semantic_candidate", "none"] = (
        "none"
    )
    semantic_score: float | None = Field(default=None, ge=-1, le=1)
    candidate_feature_id: str | None = None
    embedding_model: str | None = None
    embedding_version: str | None = None
    semantic_reason_code: str | None = None
    # Shadow-only semantic candidates. These fields are deliberately separate
    # from match_status/candidate_evidence so retrieval experiments cannot
    # change the deterministic score or recommendation.
    semantic_candidate_evidence: tuple[Evidence, ...] = ()
    semantic_candidate_score: float | None = Field(default=None, ge=-1, le=1)
    status_detail: Literal[
        "matched",
        "partial",
        "uncertain",
        "insufficient_evidence",
        "not_observed",
    ] | None = None
    ce_score: float | None = Field(default=None)
    retrieval_score: float | None = Field(default=None, ge=-1, le=1)
    threshold_margin: float | None = Field(default=None)
    top_candidates: tuple[ResponsibilityCandidate, ...] = ()


class ProjectResult(ImmutableDTO):
    """Applied Experience (综合实践证据) evidence item.

    The wire/DB key remains ``projects`` for compatibility, but the semantic is
    "did the candidate actually use the required abilities in project, internship
    or work contexts", not a fixed project-experience requirement of the JD.
    """

    requirement_id: str = Field(min_length=1)
    position_requirement: tuple[str, ...]
    candidate_experience_id: str | None
    candidate_experience: str | None
    candidate_role: str | None
    candidate_tasks: tuple[str, ...] = ()
    candidate_achievements: tuple[str, ...] = ()
    required_skill_ids: tuple[str, ...] = ()
    covered_skill_ids: tuple[str, ...] = ()
    match_status: ContextMatchStatus
    matching_rules: tuple[str, ...] = ()
    position_evidence: tuple[Evidence, ...] = ()
    candidate_evidence: tuple[Evidence, ...] = ()
    reason_code: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    match_type: Literal["deterministic", "semantic", "semantic_candidate", "none"] = (
        "none"
    )
    semantic_score: float | None = Field(default=None, ge=-1, le=1)
    candidate_feature_id: str | None = None
    embedding_model: str | None = None
    embedding_version: str | None = None
    semantic_reason_code: str | None = None


class ScenarioResult(ImmutableDTO):
    requirement_id: str = Field(min_length=1)
    scenario_type: Literal["industry", "business_scenario"]
    position_requirement: str = Field(min_length=1)
    candidate_experience_id: str | None
    candidate_experience: str | None
    match_status: ContextMatchStatus
    matching_rules: tuple[str, ...] = ()
    position_evidence: tuple[Evidence, ...] = ()
    candidate_evidence: tuple[Evidence, ...] = ()
    reason_code: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    match_type: Literal["deterministic", "semantic", "semantic_candidate", "none"] = (
        "none"
    )
    semantic_score: float | None = Field(default=None, ge=-1, le=1)
    candidate_feature_id: str | None = None
    embedding_model: str | None = None
    embedding_version: str | None = None
    semantic_reason_code: str | None = None


class DimensionScore(ImmutableDTO):
    dimension: ScoreDimension
    score: float | None = Field(default=None, ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    configured_weight: float = Field(ge=0, le=1)
    effective_weight: float = Field(ge=0, le=1)
    applicable_count: int = Field(ge=0)
    scored_count: int = Field(ge=0)
    uncertain_count: int = Field(ge=0)
    dimension_status: DimensionStatus = "scored"


class RequirementCapShare(ImmutableDTO):
    """One requirement's final weight share after two-level normalization."""

    requirement_id: str = Field(min_length=1)
    dimension: ScoreDimension
    allocated_weight: float = Field(ge=0, le=1)
    capped: bool = False


class TwoLevelNormalization(ImmutableDTO):
    """Explicit record of the two-level requirement normalization outcome.

    Guarantees:
    * ``allocated_mass + residual_mass == target_scored_mass``
    * every requirement share stays at or below ``max_requirement_share``
    * mass that cannot be allocated because the cap ceiling binds is reported
      as ``residual_mass`` instead of silently re-amplifying capped items.
    """

    version: str = Field(min_length=1)
    active: bool = False
    max_requirement_share: float = Field(ge=0, le=1)
    target_scored_mass: float = Field(ge=0, le=1)
    allocated_mass: float = Field(ge=0, le=1)
    residual_mass: float = Field(ge=0, le=1)
    cap_satisfied: bool = True
    capped_requirement_count: int = Field(default=0, ge=0)
    requirement_shares: tuple[RequirementCapShare, ...] = ()


class ScoreContribution(ImmutableDTO):
    dimension: ScoreDimension
    result_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    match_type: str | None = None
    reason_code: str = Field(min_length=1)
    score_value: float | None = Field(default=None, ge=0, le=1)
    effective_weight: float = Field(ge=0, le=1)
    weighted_points: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    position_evidence: tuple[Evidence, ...] = ()
    candidate_evidence: tuple[Evidence, ...] = ()
    relation_evidence: tuple[Evidence, ...] = ()


class ScoreInsight(ImmutableDTO):
    dimension: ScoreDimension
    result_id: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    evidence: tuple[Evidence, ...] = ()


class FinalMatchResult(ImmutableDTO):
    overall_score: float | None = Field(default=None, ge=0, le=100)
    match_confidence: float = Field(ge=0, le=1)
    recommendation_level: Literal[
        "strong_match",
        "potential_match",
        "weak_match",
        "not_recommended",
        "insufficient_information",
    ]
    hard_gate_status: Literal["passed", "failed", "uncertain", "not_applicable"]
    dimension_scores: tuple[DimensionScore, ...]
    expected_dimensions: tuple[ScoreDimension, ...] = ()
    produced_dimensions: tuple[ScoreDimension, ...] = ()
    missing_evaluation_dimensions: tuple[ScoreDimension, ...] = ()
    two_level_normalization: TwoLevelNormalization | None = None
    score_contributions: tuple[ScoreContribution, ...]
    strengths: tuple[ScoreInsight, ...]
    gaps: tuple[ScoreInsight, ...]
    uncertain_items: tuple[ScoreInsight, ...]
    explanation: str = Field(min_length=1)
    algorithm_version: str = Field(min_length=1)
    scoring_config_version: str = Field(min_length=1)
    cv_profile_id: str = Field(min_length=1)
    position_profile_id: str = Field(min_length=1)
    input_evaluation_algorithm_version: str = Field(min_length=1)
    source_evaluation_id: str = Field(min_length=1)
    cv_taxonomy_version: str = Field(min_length=1)
    cv_derivation_version: str = Field(min_length=1)
    position_taxonomy_version: str = Field(min_length=1)
    position_graph_version: str = Field(min_length=1)
    position_quality_snapshot_id: str = Field(min_length=1)
    position_trend_version: str | None = None
    vector_text_derivation_version: str | None = None
    embedding_model: str | None = None
    embedding_version: str | None = None
    semantic_algorithm_version: str | None = None
    semantic_threshold_config_version: str | None = None
    semantic_index_revision: str | None = None
    semantic_collection: str | None = None
    semantic_embedding_dimension: int | None = Field(default=None, gt=0)
    semantic_embedding_normalized: bool | None = None
    semantic_embedding_normalization: Literal["l2"] | None = None
    semantic_vector_representation: Literal["dense"] | None = None
    semantic_vector_similarity: Literal["cosine"] | None = None
    semantic_text_derivation_version: str | None = None
    semantic_weight: float = Field(default=0.0, ge=0, le=0.2)
    information_sufficient: bool = True
    information_sufficiency_level: Literal[
        "sufficient", "blocking", "material", "minor"
    ] = "sufficient"
    information_sufficiency_reasons: tuple[str, ...] = ()


class MatchEvaluation(ImmutableDTO):
    evaluation_id: str = Field(min_length=1)
    cv_profile_id: str | None = Field(default=None, min_length=1, max_length=200)
    cv_profile_version: str | None = Field(default=None, min_length=1, max_length=200)
    position_profile_id: str | None = Field(default=None, min_length=1, max_length=200)
    position_profile_version: str | None = Field(default=None, min_length=1, max_length=200)
    algorithm_version: str = Field(min_length=1)
    evaluation_status: EvaluationStatus
    error_code: str | None = None
    error_message: str | None = None
    hard_constraint_results: tuple[HardConstraintResult, ...] = ()
    skill_results: tuple[SkillResult, ...] = ()
    responsibility_results: tuple[ResponsibilityResult, ...] = ()
    project_results: tuple[ProjectResult, ...] = ()
    scenario_results: tuple[ScenarioResult, ...] = ()
    requirement_group_results: tuple[RequirementGroupResult, ...] = ()
    required_skill_coverage: float | None = Field(default=None, ge=0, le=1)
    bonus_skill_coverage: float | None = Field(default=None, ge=0, le=1)
    hard_constraint_pass_rate: float | None = Field(default=None, ge=0, le=1)
    required_transferable_coverage: float | None = Field(default=None, ge=0, le=1)
    bonus_transferable_coverage: float | None = Field(default=None, ge=0, le=1)
    responsibility_coverage: float | None = Field(default=None, ge=0, le=1)
    project_coverage: float | None = Field(default=None, ge=0, le=1)
    scenario_coverage: float | None = Field(default=None, ge=0, le=1)
    input_coverage: dict[str, object] = Field(default_factory=dict)
    vector_profile_version: str | None = Field(default=None, min_length=1, max_length=200)
    vector_text_derivation_version: str | None = None
    embedding_model: str | None = None
    embedding_version: str | None = None
    semantic_algorithm_version: str | None = None
    threshold_config_version: str | None = None
    semantic_status: Literal["disabled", "available", "unavailable"] = "disabled"
    semantic_error_code: str | None = None
    semantic_shadow_score: float | None = Field(default=None, ge=-1, le=1)
    semantic_shadow_evidence: tuple[SemanticRetrievalEvidence, ...] = ()
    semantic_candidates: tuple[SemanticCandidate, ...] = ()
    semantic_shadow_status: Literal["disabled", "available", "unavailable"] = "disabled"
    semantic_latency_ms: float | None = Field(default=None, ge=0)
    semantic_retrieval_trace_id: str | None = None
    semantic_embedding_model: str | None = None
    semantic_embedding_revision: str | None = None
    semantic_embedding_dimension: int | None = Field(default=None, gt=0)
    semantic_embedding_normalized: bool | None = None
    semantic_embedding_normalization: Literal["l2"] | None = None
    semantic_vector_representation: Literal["dense"] | None = None
    semantic_vector_similarity: Literal["cosine"] | None = None
    semantic_text_derivation_version: str | None = None
    semantic_index_revision: str | None = None
    semantic_collection: str | None = None
    semantic_score: float | None = Field(default=None, ge=-1, le=1)
    semantic_weight: float = Field(default=0.0, ge=0, le=0.2)
    semantic_effective_weight: float = Field(default=0.0, ge=0, le=0.2)
    semantic_evidence: tuple[SemanticRetrievalEvidence, ...] = ()
    semantic_explanations: tuple[SemanticMatchExplanation, ...] = ()
    semantic_target_type: Literal["standard_position", "enterprise_job"] | None = None
    semantic_stale: bool = False
    semantic_llm_status: Literal["disabled", "available", "unavailable"] = "disabled"
    semantic_llm_error_code: str | None = None
    semantic_llm_model: str | None = None
    semantic_llm_algorithm_version: str | None = None
    semantic_llm_candidates: tuple[SkillSemanticCandidate, ...] = ()
    unresolved_count: int = Field(default=0, ge=0)
    unknown_count: int = Field(default=0, ge=0)
    information_sufficient: bool = True
    information_sufficiency_level: Literal[
        "sufficient", "blocking", "material", "minor"
    ] = "sufficient"
    information_sufficiency_reasons: tuple[str, ...] = ()
    summary: EvaluationSummary | None = None
    final_match_result: FinalMatchResult | None = None

    @model_validator(mode="after")
    def enforce_shadow_status(self) -> MatchEvaluation:
        if self.semantic_status == "unavailable" and not self.semantic_error_code:
            raise ValueError("semantic unavailable status requires semantic_error_code")
        if self.semantic_shadow_status == "unavailable":
            if (
                self.semantic_shadow_score is not None
                or self.semantic_shadow_evidence
                or self.semantic_candidates
            ):
                raise ValueError("unavailable semantic shadow cannot contain candidates")
        if self.semantic_shadow_status == "available" and self.semantic_error_code:
            raise ValueError("available semantic shadow cannot contain an error code")
        if self.semantic_shadow_status == "disabled" and (
            self.semantic_shadow_score is not None or self.semantic_shadow_evidence
        ):
            raise ValueError("disabled semantic shadow cannot contain retrieval data")
        return self
