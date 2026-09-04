from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


BFFTaskStatus = Literal["pending", "running", "succeeded", "failed", "cancelled"]
BFFReportStatus = Literal[
    "pending", "running", "succeeded", "failed", "cancelled", "current", "stale"
]
BFFResultStatus = Literal["completed", "empty", "failed", "cancelled", "insufficient_data"]
EvaluationStatus = Literal["completed", "rejected", "failed"]
GapType = Literal[
    "hard_constraint_gap",
    "required_skill_missing",
    "bonus_skill_missing",
    "skill_level_gap",
    "evidence_gap",
    "usage_evidence_gap",
    "ownership_gap",
    "responsibility_gap",
    "project_gap",
    "scenario_gap",
    "requirement_group_gap",
    "unresolved_gap",
]
GapPriority = Literal["critical", "high", "medium", "low"]


class BFFResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceVersionResponse(BFFResponseModel):
    validated_cv_snapshot_id: str | None = None
    source_cv_version_id: str | None = None
    resume_id: str | None = None
    position_id: str | None = None
    graph_version: str | None = None
    source_jd_version_id: str | None = None
    evaluation_id: str | None = None


class EvidenceResponse(BFFResponseModel):
    source_object_type: str
    source_object_id: str
    source_document_id: str | None = None
    source_fragment_id: str
    quote: str
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)
    alignment: str = "unresolved"
    occurrence_index: int | None = Field(default=None, ge=0)
    version: EvidenceVersionResponse = Field(default_factory=EvidenceVersionResponse)
    result_reference: str


class SemanticRetrievalEvidenceResponse(BFFResponseModel):
    query_fragment_id: str
    candidate_fragment_id: str
    query_fragment_type: str
    candidate_fragment_type: str
    candidate_source_id: str
    similarity: float = Field(ge=-1, le=1)
    rank: int = Field(ge=1)
    dense_rank: int | None = Field(default=None, ge=1)
    sparse_rank: int | None = Field(default=None, ge=1)
    rrf_score: float = Field(default=0.0, ge=0)
    retrieval_score: float | None = Field(default=None, ge=-1, le=1)
    rerank_score: float | None = Field(default=None, ge=-1, le=1)
    final_rank: int | None = Field(default=None, ge=1)
    evidence_ref: EvidenceResponse
    position_evidence_ref: EvidenceResponse
    profile_version: str | None = None
    embedding_model: str = "embedding.unknown"
    embedding_revision: str
    embedding_dimension: int = Field(default=1024, gt=0)
    embedding_normalized: bool = True
    embedding_normalization: Literal["l2"] | None = None
    vector_representation: Literal["dense"] | None = None
    vector_similarity: Literal["cosine"] | None = None
    text_derivation_version: str | None = None
    index_revision: str | None = None
    collection: str | None = None
    reranker_model_revision: str | None = None
    retrieval_trace_id: str


class SemanticCandidateResponse(BFFResponseModel):
    candidate_source_id: str
    score: float = Field(ge=-1, le=1)
    evidence: list[SemanticRetrievalEvidenceResponse] = Field(default_factory=list)
    retrieval_score: float | None = Field(default=None, ge=-1, le=1)
    rerank_score: float | None = Field(default=None, ge=-1, le=1)
    final_rank: int | None = Field(default=None, ge=1)
    reranker_model_revision: str | None = None
    degraded: bool = False
    degradation_reason: str | None = None


class SemanticMatchExplanationResponse(BFFResponseModel):
    dimension: Literal[
        "skill_semantic_match",
        "responsibility_semantic_match",
        "project_semantic_match",
        "scenario_semantic_match",
    ]
    match_kind: Literal["semantic_related"] = "semantic_related"
    score: float = Field(ge=-1, le=1)
    position_text: str
    resume_evidence: str
    evidence_ref: str
    embedding_revision: str


class SkillSemanticCandidateResponse(BFFResponseModel):
    requirement_id: str
    required_skill_id: str
    required_skill_name: str
    candidate_skill_id: str
    candidate_skill_name: str
    proposed_relation_type: str
    relation_type: str
    status: Literal["valid", "unknown"]
    reason_code: str
    confidence: float = Field(ge=0, le=1)
    position_evidence: list[EvidenceResponse] = Field(default_factory=list)
    candidate_evidence: list[EvidenceResponse] = Field(default_factory=list)
    relation_evidence: list[EvidenceResponse] = Field(default_factory=list)
    relation_source: str | None = None
    relation_graph_version: str | None = None
    model: str
    algorithm_version: str


class HardConstraintResultResponse(BFFResponseModel):
    requirement_id: str
    constraint_type: Literal[
        "education",
        "experience",
        "certificate",
        "language",
        "location",
        "availability",
    ]
    status: Literal["pass", "partial", "fail", "unknown", "unresolved", "not_required"]
    required_value: str | None = None
    candidate_value: str | None = None
    position_evidence: list[EvidenceResponse] = Field(default_factory=list)
    candidate_evidence: list[EvidenceResponse] = Field(default_factory=list)
    reason_code: str
    confidence: float = Field(ge=0, le=1)


class MatchSkillResultResponse(BFFResponseModel):
    requirement_id: str
    skill_id: str | None = None
    skill_name: str | None = None
    importance_level: Literal["required", "bonus"]
    requirement_weight: float = Field(default=1.0, ge=0, le=1)
    required_level: str | None = None
    candidate_declared_level: str | None = None
    candidate_demonstrated_level: str | None = None
    verification_status: str | None = None
    match_status: Literal[
        "matched", "partial", "weak", "declared_only", "missing", "unknown", "unresolved"
    ]
    position_evidence: list[EvidenceResponse] = Field(default_factory=list)
    candidate_evidence: list[EvidenceResponse] = Field(default_factory=list)
    reason_code: str
    confidence: float = Field(ge=0, le=1)
    match_type: Literal[
        "exact",
        "equivalent",
        "parent_child",
        "prerequisite",
        "related",
        "transferable",
        "semantic_candidate",
        "none",
    ] = "none"
    related_candidate_skill_id: str | None = None
    prerequisite_skill_ids: list[str] = Field(default_factory=list)
    relation_type: str | None = None
    relation_confidence: float | None = Field(default=None, ge=0, le=1)
    relation_evidence: list[EvidenceResponse] = Field(default_factory=list)
    relation_source: str | None = None
    relation_graph_version: str | None = None
    transferability_score: float = Field(default=0.0, ge=0, le=1)
    semantic_model: str | None = None
    semantic_algorithm_version: str | None = None
    semantic_candidate_id: str | None = None
    candidate_ownership: str | None = None
    required_ownership: str | None = None


class ResponsibilityCandidateResponse(BFFResponseModel):
    experience_id: str
    text: str
    retrieval_score: float | None = Field(default=None, ge=-1, le=1)
    ce_score: float | None = None
    threshold_margin: float | None = None
    evidence_refs: list[EvidenceResponse] = Field(default_factory=list)


class ResponsibilityResultResponse(BFFResponseModel):
    requirement_id: str
    position_requirement: str
    candidate_experience_id: str | None = None
    candidate_experience: str | None = None
    match_status: Literal[
        "matched", "partial", "not_observed", "unknown", "unresolved"
    ]
    status_detail: Literal[
        "matched",
        "partial",
        "uncertain",
        "insufficient_evidence",
        "not_observed",
    ] | None = None
    matching_rules: list[str] = Field(default_factory=list)
    position_evidence: list[EvidenceResponse] = Field(default_factory=list)
    candidate_evidence: list[EvidenceResponse] = Field(default_factory=list)
    reason_code: str
    confidence: float = Field(ge=0, le=1)
    match_type: Literal["deterministic", "semantic", "semantic_candidate", "none"] = "none"
    semantic_score: float | None = Field(default=None, ge=-1, le=1)
    candidate_feature_id: str | None = None
    embedding_model: str | None = None
    embedding_version: str | None = None
    semantic_reason_code: str | None = None
    ce_score: float | None = None
    retrieval_score: float | None = Field(default=None, ge=-1, le=1)
    threshold_margin: float | None = None
    top_candidates: list[ResponsibilityCandidateResponse] = Field(
        default_factory=list
    )


class ProjectResultResponse(BFFResponseModel):
    """Applied Experience (综合实践证据) evidence channel.

    The wire key is ``projects`` for backward compatibility (frozen artifacts and
    older callers). Display semantics: whether the candidate actually used the
    abilities required by the position in project / internship / work contexts.
    It is NOT a fixed "project-experience requirement" of the JD.
    """

    requirement_id: str
    position_requirement: list[str] = Field(default_factory=list)
    candidate_experience_id: str | None = None
    candidate_experience: str | None = None
    candidate_role: str | None = None
    candidate_tasks: list[str] = Field(default_factory=list)
    candidate_achievements: list[str] = Field(default_factory=list)
    required_skill_ids: list[str] = Field(default_factory=list)
    covered_skill_ids: list[str] = Field(default_factory=list)
    match_status: Literal["matched", "partial", "not_observed", "unknown", "unresolved"]
    matching_rules: list[str] = Field(default_factory=list)
    position_evidence: list[EvidenceResponse] = Field(default_factory=list)
    candidate_evidence: list[EvidenceResponse] = Field(default_factory=list)
    reason_code: str
    confidence: float = Field(ge=0, le=1)
    match_type: Literal["deterministic", "semantic", "semantic_candidate", "none"] = "none"
    semantic_score: float | None = Field(default=None, ge=-1, le=1)
    candidate_feature_id: str | None = None
    embedding_model: str | None = None
    embedding_version: str | None = None
    semantic_reason_code: str | None = None


class ScenarioResultResponse(BFFResponseModel):
    requirement_id: str
    scenario_type: Literal["industry", "business_scenario"]
    position_requirement: str
    candidate_experience_id: str | None = None
    candidate_experience: str | None = None
    match_status: Literal["matched", "partial", "not_observed", "unknown", "unresolved"]
    matching_rules: list[str] = Field(default_factory=list)
    position_evidence: list[EvidenceResponse] = Field(default_factory=list)
    candidate_evidence: list[EvidenceResponse] = Field(default_factory=list)
    reason_code: str
    confidence: float = Field(ge=0, le=1)
    match_type: Literal["deterministic", "semantic", "semantic_candidate", "none"] = "none"
    semantic_score: float | None = Field(default=None, ge=-1, le=1)
    candidate_feature_id: str | None = None
    embedding_model: str | None = None
    embedding_version: str | None = None
    semantic_reason_code: str | None = None


class RequirementGroupResultResponse(BFFResponseModel):
    group_id: str
    group_type: Literal["must", "should", "and", "or", "one_of", "min_count"]
    priority: Literal["required", "preferred", "bonus", "unknown"]
    status: Literal["satisfied", "partial", "unsatisfied", "unknown", "unresolved"]
    required_count: int = Field(ge=0)
    satisfied_count: int = Field(ge=0)
    evaluable_count: int = Field(ge=0)
    child_result_ids: list[str] = Field(default_factory=list)
    covered_result_ids: list[str] = Field(default_factory=list)
    covered_dimensions: list[str] = Field(default_factory=list)
    is_root: bool = False
    score: float | None = Field(default=None, ge=0, le=1)
    reason_code: str
    confidence: float = Field(ge=0, le=1)
    position_evidence: list[EvidenceResponse] = Field(default_factory=list)


class DimensionScoreResponse(BFFResponseModel):
    dimension: Literal[
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
    score: float | None = Field(default=None, ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    configured_weight: float = Field(ge=0, le=1)
    effective_weight: float = Field(ge=0, le=1)
    applicable_count: int = Field(ge=0)
    scored_count: int = Field(ge=0)
    uncertain_count: int = Field(ge=0)


class ScoreContributionResponse(BFFResponseModel):
    dimension: Literal[
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
    result_id: str
    status: str
    match_type: str | None = None
    reason_code: str
    score_value: float | None = Field(default=None, ge=0, le=1)
    effective_weight: float = Field(ge=0, le=1)
    weighted_points: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    position_evidence: list[EvidenceResponse] = Field(default_factory=list)
    candidate_evidence: list[EvidenceResponse] = Field(default_factory=list)
    relation_evidence: list[EvidenceResponse] = Field(default_factory=list)


class ScoreInsightResponse(BFFResponseModel):
    dimension: Literal[
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
    result_id: str
    reason_code: str
    message: str
    evidence: list[EvidenceResponse] = Field(default_factory=list)


class FinalMatchResultResponse(BFFResponseModel):
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
    dimension_scores: list[DimensionScoreResponse] = Field(default_factory=list)
    score_contributions: list[ScoreContributionResponse] = Field(default_factory=list)
    strengths: list[ScoreInsightResponse] = Field(default_factory=list)
    gaps: list[ScoreInsightResponse] = Field(default_factory=list)
    uncertain_items: list[ScoreInsightResponse] = Field(default_factory=list)
    explanation: str
    algorithm_version: str
    scoring_config_version: str
    cv_profile_id: str | None = None
    position_profile_id: str | None = None
    input_evaluation_algorithm_version: str
    source_evaluation_id: str | None = None
    cv_taxonomy_version: str
    cv_derivation_version: str
    position_taxonomy_version: str
    position_graph_version: str
    position_quality_snapshot_id: str
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


class EvaluationSummaryResponse(BFFResponseModel):
    hard_constraint_pass_count: int = Field(ge=0)
    hard_constraint_fail_count: int = Field(ge=0)
    required_skill_matched_count: int = Field(ge=0)
    required_skill_missing_count: int = Field(ge=0)
    bonus_skill_matched_count: int = Field(ge=0)
    bonus_skill_missing_count: int = Field(ge=0)
    coverage_denominator_policy: str = "exclude_unknown_unresolved_and_not_required"


class MatchingEvaluationResponse(BFFResponseModel):
    evaluation_id: str
    cv_profile_id: str | None = None
    cv_profile_version: str | None = None
    position_profile_id: str | None = None
    position_profile_version: str | None = None
    algorithm_version: str
    evaluation_status: EvaluationStatus | None = None
    error_code: str | None = None
    error_message: str | None = None
    hard_constraint_results: list[HardConstraintResultResponse] = Field(default_factory=list)
    skill_results: list[MatchSkillResultResponse] = Field(default_factory=list)
    responsibility_results: list[ResponsibilityResultResponse] = Field(default_factory=list)
    project_results: list[ProjectResultResponse] = Field(default_factory=list)
    scenario_results: list[ScenarioResultResponse] = Field(default_factory=list)
    requirement_group_results: list[RequirementGroupResultResponse] = Field(default_factory=list)
    required_skill_coverage: float | None = Field(default=None, ge=0, le=1)
    bonus_skill_coverage: float | None = Field(default=None, ge=0, le=1)
    hard_constraint_pass_rate: float | None = Field(default=None, ge=0, le=1)
    required_transferable_coverage: float | None = Field(default=None, ge=0, le=1)
    bonus_transferable_coverage: float | None = Field(default=None, ge=0, le=1)
    responsibility_coverage: float | None = Field(default=None, ge=0, le=1)
    project_coverage: float | None = Field(default=None, ge=0, le=1)
    scenario_coverage: float | None = Field(default=None, ge=0, le=1)
    input_coverage: dict[str, object] = Field(default_factory=dict)
    vector_profile_version: str | None = None
    vector_text_derivation_version: str | None = None
    embedding_model: str | None = None
    embedding_version: str | None = None
    semantic_algorithm_version: str | None = None
    threshold_config_version: str | None = None
    semantic_status: Literal["disabled", "available", "unavailable"] = "disabled"
    semantic_error_code: str | None = None
    semantic_shadow_score: float | None = Field(default=None, ge=-1, le=1)
    semantic_shadow_evidence: list[SemanticRetrievalEvidenceResponse] = Field(default_factory=list)
    semantic_candidates: list[SemanticCandidateResponse] = Field(default_factory=list)
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
    semantic_evidence: list[SemanticRetrievalEvidenceResponse] = Field(default_factory=list)
    semantic_explanations: list[SemanticMatchExplanationResponse] = Field(default_factory=list)
    semantic_target_type: Literal["standard_position", "enterprise_job"] | None = None
    semantic_stale: bool = False
    semantic_llm_status: Literal["disabled", "available", "unavailable"] = "disabled"
    semantic_llm_error_code: str | None = None
    semantic_llm_model: str | None = None
    semantic_llm_algorithm_version: str | None = None
    semantic_llm_candidates: list[SkillSemanticCandidateResponse] = Field(default_factory=list)
    unresolved_count: int = Field(default=0, ge=0)
    unknown_count: int = Field(default=0, ge=0)
    summary: EvaluationSummaryResponse | None = None
    final_match_result: FinalMatchResultResponse | None = None


class PrerequisiteStateResponse(BFFResponseModel):
    skill_id: str
    status: Literal["satisfied", "missing", "unknown"]
    source: Literal["candidate_profile", "evaluation", "unavailable"]
    evidence_refs: list[EvidenceResponse] = Field(default_factory=list)


class LearningStepResponse(BFFResponseModel):
    step_order: int = Field(ge=1)
    source_action_id: str | None = None
    target_skill_id: str | None = None
    objective: str
    prerequisite_skill_ids: list[str] = Field(default_factory=list)
    basis: list[str] = Field(default_factory=list)
    estimated_hours: float = Field(ge=0)
    cost_source_type: Literal[
        "dataset_backed", "expert_estimate", "manual", "heuristic", "unknown"
    ] = "unknown"
    cost_source_ref: str | None = None
    estimate_status: Literal["verified", "estimated", "unknown"] = "unknown"
    cost_model: str = Field(default="gap-learning-hours.v1", min_length=1)
    completion_criteria: list[str] = Field(default_factory=list)
    source_requirement_ids: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    prerequisite_states: list[PrerequisiteStateResponse] = Field(default_factory=list)
    planning_status: Literal["ready", "blocked"] = "ready"
    blocked_reason_codes: list[str] = Field(default_factory=list)


class CounterfactualSuggestionResponse(BFFResponseModel):
    requirement_id: str
    skill_id: str | None = None
    suggestion: str
    basis_evidence: list[EvidenceResponse] = Field(default_factory=list)


class PrioritizedGapResponse(BFFResponseModel):
    gap_type: GapType
    requirement_id: str
    skill_id: str | None = None
    current_level: str | None = None
    target_level: str | None = None
    priority: GapPriority
    priority_score: float = Field(ge=0, le=100)
    reason_codes: list[str] = Field(default_factory=list)
    evidence: list[EvidenceResponse] = Field(default_factory=list)
    position_evidence_present: bool = False
    candidate_evidence_present: bool = False
    source_match_type: str | None = None
    transferable_skill_ids: list[str] = Field(default_factory=list)
    transferability_score: float = Field(default=0.0, ge=0, le=1)
    prerequisite_skill_ids: list[str] = Field(default_factory=list)
    current_ownership: str | None = None
    target_ownership: str | None = None
    score_effect_status: Literal["modeled", "not_modeled_in_v1"] = "modeled"


class CostBandResponse(BFFResponseModel):
    min_hours: float = Field(ge=0)
    expected_hours: float = Field(ge=0)
    max_hours: float = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    basis: str


class WhatIfActionRequest(BFFResponseModel):
    action_id: str
    action_type: Literal[
        "add_skill",
        "add_project_experience",
        "strengthen_evidence",
        "strengthen_ownership",
        "satisfy_hard_condition",
        "controlled_skill_transfer",
    ]
    skill_id: str | None = None
    canonical_name: str | None = None
    learning_title: str | None = None
    target_level: str | None = None
    ownership: str | None = None
    target_requirement_ids: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    business_scenarios: list[str] = Field(default_factory=list)
    path_refs: list[str] = Field(default_factory=list)
    estimated_hours: float = Field(default=0.0, ge=0)
    cost_band: CostBandResponse | None = None
    stage: (
        Literal[
            "foundation",
            "proficiency",
            "evidence",
            "project",
            "ownership",
            "context",
            "hard_gate",
            "transfer",
        ]
        | None
    ) = None
    requires_action_ids: list[str] = Field(default_factory=list)
    supersedes_action_ids: list[str] = Field(default_factory=list)
    cost_model: str = "heuristic_level_distance.v1"
    estimated_score_delta: float | None = None
    estimated_utility: float | None = None
    score_effect_reason: str | None = None
    milestone_status: str | None = None
    deliverable: str | None = None
    acceptance_criteria: list[str] = Field(default_factory=list)


class WhatIfCreate(BFFResponseModel):
    actions: list[WhatIfActionRequest] = Field(max_length=12)


class EvidenceDeletionCreate(BFFResponseModel):
    deletion_kind: Literal["critical", "noncritical"]
    evidence_source_ids: list[str] = Field(min_length=1, max_length=32)


class DimensionDeltaResponse(BFFResponseModel):
    dimension: str
    baseline_score: float | None = None
    scenario_score: float | None = None
    delta: float | None = None


class WhatIfResponse(BFFResponseModel):
    # Outcome boundary: this is a modeled counterfactual re-score, not observed
    # real-world learning after completing the actions.
    outcome_semantics: Literal["modeled_counterfactual"] = "modeled_counterfactual"
    observed_outcome: Literal[False] = False

    generation_status: Literal["completed", "rejected"]
    scenario_id: str
    baseline_evaluation: MatchingEvaluationResponse | None = None
    scenario_evaluation: MatchingEvaluationResponse | None = None
    projected_evaluation: MatchingEvaluationResponse | None = None
    actions: list[WhatIfActionRequest] = Field(default_factory=list)
    baseline_score: float | None = None
    # Primary modeled-contract fields.
    modeled_final_score: float | None = None
    modeled_score_delta: float | None = None
    modeled_confidence_delta: float | None = None
    # Deprecated aliases (same values) kept for compatibility.
    scenario_score: float | None = None
    score_delta: float | None = None
    baseline_confidence: float | None = None
    scenario_confidence: float | None = None
    confidence_delta: float | None = None
    baseline_recommendation: str | None = None
    scenario_recommendation: str | None = None
    baseline_hard_gate_status: str | None = None
    scenario_hard_gate_status: str | None = None
    dimension_deltas: list[DimensionDeltaResponse] = Field(default_factory=list)
    denominator_changed: bool = False
    score_effect_status: Literal["modeled", "not_modeled_in_v1"] = "modeled"
    baseline_evaluation_id: str | None = None
    scoring_algorithm_version: str | None = None
    scoring_config_version: str | None = None
    position_graph_version: str | None = None
    target_type: Literal["standard_position", "enterprise_job"] | None = None
    use_enterprise_weights: bool | None = None
    hypothetical: bool = True
    algorithm_version: str
    error_code: str | None = None
    error_message: str | None = None
    projected_if_completed: bool = False
    projected_actions: list[WhatIfActionRequest] = Field(default_factory=list)
    projected_score: float | None = None
    projected_score_delta: float | None = None
    projected_confidence: float | None = None
    projected_recommendation: str | None = None
    projected_hard_gate_status: str | None = None
    current_verified_outcome: str | None = None
    projected_if_completed_outcome: str | None = None


class ExplanationFactorResponse(BFFResponseModel):
    factor_id: str
    factor_type: str
    requirement_id: str | None = None
    reason_code: str
    criticality: Literal["critical", "noncritical"]
    evidence_source_ids: list[str] = Field(default_factory=list)
    used_by_scorer: bool
    evidence_supported: bool


class EvidenceDeletionResponse(BFFResponseModel):
    generation_status: Literal["completed", "rejected"]
    deletion_run_id: str
    deletion_kind: Literal["critical", "noncritical"] | None = None
    deleted_evidence_source_ids: list[str] = Field(default_factory=list)
    critical_evidence_source_ids: list[str] = Field(default_factory=list)
    noncritical_evidence_source_ids: list[str] = Field(default_factory=list)
    explanation_factors: list[ExplanationFactorResponse] = Field(default_factory=list)
    baseline_evaluation: MatchingEvaluationResponse | None = None
    ablated_evaluation: MatchingEvaluationResponse | None = None
    baseline_gap_analysis: GapAnalysisResponse | None = None
    ablated_gap_analysis: GapAnalysisResponse | None = None
    baseline_score: float | None = None
    ablated_score: float | None = None
    retained_only_score: float | None = None
    score_delta: float | None = None
    dimension_deltas: list[DimensionDeltaResponse] = Field(default_factory=list)
    baseline_hard_gate_status: str | None = None
    ablated_hard_gate_status: str | None = None
    hard_gate_delta: str | None = None
    added_gap_ids: list[str] = Field(default_factory=list)
    removed_gap_ids: list[str] = Field(default_factory=list)
    added_action_ids: list[str] = Field(default_factory=list)
    removed_action_ids: list[str] = Field(default_factory=list)
    comprehensiveness: float | None = None
    sufficiency: float | None = None
    unsupported_reason_rate: float = Field(ge=0, le=1)
    faithfulness_status: Literal[
        "faithful", "possibly_unfaithful", "unstable", "not_applicable"
    ]
    baseline_evaluation_id: str | None = None
    cv_profile_version: str | None = None
    position_profile_version: str | None = None
    scoring_algorithm_version: str | None = None
    scoring_config_version: str | None = None
    classification_policy_version: str
    stability_threshold_points: float = Field(ge=0)
    hypothetical: bool = True
    algorithm_version: str
    error_code: str | None = None
    error_message: str | None = None


class LearningRouteResponse(BFFResponseModel):
    # Outcome boundary: modeled counterfactual re-score, not observed learning.
    outcome_semantics: Literal["modeled_counterfactual"] = "modeled_counterfactual"
    observed_outcome: Literal[False] = False

    route_type: Literal["fastest_employment", "budget_max_gain", "foundation_first"]
    action_ids: list[str] = Field(min_length=1)
    total_cost_hours: float = Field(ge=0)
    baseline_score: float | None = None
    # Primary modeled-contract fields.
    modeled_final_score: float | None = None
    modeled_score_delta: float | None = None
    modeled_confidence_delta: float | None = None
    # Deprecated aliases (same values) kept for compatibility.
    final_score: float | None = None
    projected_match_gain: float | None = None
    confidence_gain: float | None = None
    target_reachable: bool
    final_recommendation: str | None = None
    remaining_blocker_ids: list[str] = Field(default_factory=list)
    path_refs: list[str] = Field(default_factory=list)
    # Explicit per-route cost provenance; kept optional for compatibility.
    action_costs: list[ActionCostResponse] = Field(default_factory=list)
    # Modeled scenario dimension scores for the radar comparison panel;
    # the baseline panel reuses the report dimension scores.
    scenario_dimension_scores: list[DimensionScoreResponse] = Field(default_factory=list)
    algorithm_version: str


class ActionCostResponse(BFFResponseModel):
    action_id: str
    direct_hours: float = Field(ge=0)
    dependency_hours: float = Field(ge=0)
    total_hours: float = Field(ge=0)
    difficulty: Literal["low", "medium", "high"]
    selected: bool
    cost_model: str
    cost_source_type: Literal[
        "dataset_backed", "expert_estimate", "manual", "heuristic", "unknown"
    ] = "unknown"
    cost_source_ref: str | None = None
    estimate_status: Literal["verified", "estimated", "unknown"] = "unknown"


class MinimalActionSetResponse(BFFResponseModel):
    # Outcome boundary: modeled counterfactual re-score, not observed learning.
    outcome_semantics: Literal["modeled_counterfactual"] = "modeled_counterfactual"
    observed_outcome: Literal[False] = False

    status: Literal[
        "reached",
        "already_satisfied",
        "hard_blocked",
        "position_evidence_insufficient",
        "no_positive_actions",
        "budget_excluded",
        "unreachable",
    ]
    source_evaluation_id: str
    scenario_id: str | None = None
    selected_action_ids: list[str] = Field(default_factory=list)
    deferred_action_ids: list[str] = Field(default_factory=list)
    action_costs: list[ActionCostResponse] = Field(default_factory=list)
    minimum_action_count: int = Field(ge=0)
    total_cost_hours: float = Field(ge=0)
    budget_hours: float | None = Field(default=None, ge=0)
    budget_used_hours: float = Field(ge=0)
    budget_remaining_hours: float | None = Field(default=None, ge=0)
    baseline_score: float | None = None
    # Primary modeled-contract fields.
    modeled_final_score: float | None = None
    modeled_score_delta: float | None = None
    modeled_confidence_delta: float | None = None
    # Deprecated aliases (same values) kept for compatibility.
    scenario_score: float | None = None
    score_delta: float | None = None
    dimension_deltas: list[DimensionDeltaResponse] = Field(default_factory=list)
    baseline_hard_gate_status: str | None = None
    scenario_hard_gate_status: str | None = None
    hard_gate_delta: str | None = None
    target_reachable: bool
    covered_requirement_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceResponse] = Field(default_factory=list)
    path_refs: list[str] = Field(default_factory=list)
    unreachable_reason_codes: list[str] = Field(default_factory=list)
    cv_profile_version: str | None = None
    position_profile_version: str | None = None
    graph_version_id: str
    policy_version: str
    search_status: Literal["exact_bounded", "bounded_beam"] = "exact_bounded"
    algorithm_version: str


class SkillPathEdgeResponse(BFFResponseModel):
    relation_id: str
    source_skill_id: str
    target_skill_id: str
    relation_type: Literal["equivalent", "parent_child", "related", "transferable"]
    graph_version: str
    confidence: float = Field(ge=0, le=1)
    hop_number: int = Field(ge=1, le=2)
    edge_cost_hours: float = Field(ge=0)
    evidence_refs: list[EvidenceResponse] = Field(default_factory=list)


class SkillTransferPathResponse(BFFResponseModel):
    path_id: str
    source_skill_id: str
    target_skill_id: str
    target_requirement_id: str
    node_skill_ids: list[str] = Field(min_length=2)
    edges: list[SkillPathEdgeResponse] = Field(min_length=1, max_length=2)
    hop_count: int = Field(ge=1, le=2)
    total_cost_hours: float = Field(ge=0)
    minimum_confidence: float = Field(ge=0, le=1)
    effective_confidence: float = Field(ge=0, le=1)
    outcome_status: Literal["eligible", "partial"]
    graph_version_id: str
    cost_model: str


class SkillPathDecisionResponse(BFFResponseModel):
    target_requirement_id: str
    target_skill_id: str
    status: Literal["reachable", "unreachable"]
    paths: list[SkillTransferPathResponse] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    max_hops: int = Field(ge=1, le=2)
    max_cost_hours: float = Field(ge=0)
    relation_whitelist: list[str] = Field(default_factory=list)
    source_status: Literal["available", "unavailable", "error"]
    algorithm_version: str


class GapAnalysisResponse(BFFResponseModel):
    generation_status: Literal["completed", "rejected"] | None = None
    result_status: BFFResultStatus | None = None
    prioritized_gaps: list[PrioritizedGapResponse] = Field(default_factory=list)
    learning_path: list[LearningStepResponse] = Field(default_factory=list)
    counterfactual_suggestions: list[CounterfactualSuggestionResponse] = Field(default_factory=list)
    candidate_actions: list[WhatIfActionRequest] = Field(default_factory=list)
    learning_routes: list[LearningRouteResponse] = Field(default_factory=list)
    minimal_action_set: MinimalActionSetResponse | None = None
    skill_path_decisions: list[SkillPathDecisionResponse] = Field(default_factory=list)
    time_budget_hours: float | None = Field(default=None, ge=0)
    over_budget: bool = False
    estimated_readiness: float | None = Field(default=None, ge=0, le=1)
    algorithm_version: str | None = None
    config_version: str | None = None
    gap_policy_version: str | None = None
    gap_policy_hash: str | None = None
    source_evaluation_algorithm_version: str | None = None
    source_scoring_algorithm_version: str | None = None
    source_scoring_config_version: str | None = None
    semantic_algorithm_version: str | None = None
    embedding_version: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class AlgorithmVersionsResponse(BFFResponseModel):
    evaluation: str | None = None
    scoring: str | None = None
    scoring_config: str | None = None
    gap: str | None = None
    gap_config: str | None = None
    semantic: str | None = None


class DataVersionsResponse(BFFResponseModel):
    cv_source: str | None = None
    position_source: str | None = None
    cv_taxonomy: str | None = None
    position_taxonomy: str | None = None
    graph: str | None = None
    embedding: str | None = None


class MatchVersionsResponse(BFFResponseModel):
    schema_version: str | None = None
    profile_contract_mapping_version: str | None = None
    graph_version: str | None = None
    embedding_model: str | None = None
    embedding_version: str | None = None
    embedding_dimension: int | None = Field(default=None, ge=0)
    vector_text_derivation_version: str | None = None
    semantic_algorithm_version: str | None = None
    semantic_threshold_version: str | None = None
    evaluation_algorithm_version: str | None = None
    scoring_algorithm_version: str | None = None
    scoring_config_version: str | None = None
    gap_algorithm_version: str | None = None
    gap_config_version: str | None = None
    semantic_index_revision: str | None = None
    target_type: str | None = None
    use_enterprise_weights: bool | None = None
    generate_learning_path: bool | None = None
    cv_source_version: str | None = None
    position_source_version: str | None = None
    cv_taxonomy_version: str | None = None
    position_taxonomy_version: str | None = None
    position_graph_version: str | None = None


class MatchReportLineage(BFFResponseModel):
    resume_id: str | None = None
    position_id: str | None = None
    position_name: str | None = None
    validated_cv_snapshot_id: str | None = None
    target_type: str | None = None
    provider: str | None = None
    method: str | None = None
    algorithm_versions: AlgorithmVersionsResponse | None = None
    data_versions: DataVersionsResponse | None = None


class MatchReportResponse(BFFResponseModel):
    evaluation_id: str
    task_id: str | None = None
    status: BFFReportStatus | None = None
    result_status: BFFResultStatus | None = None
    matching_method: Literal["rule", "semantic_verified", "unknown"] = "unknown"
    degraded: bool = False
    stale: bool = False
    stale_reason_codes: list[str] = Field(default_factory=list)
    evaluation: MatchingEvaluationResponse = Field(default_factory=MatchingEvaluationResponse)
    gap_analysis: GapAnalysisResponse = Field(default_factory=GapAnalysisResponse)
    versions: MatchVersionsResponse = Field(default_factory=MatchVersionsResponse)
    lineage: MatchReportLineage | None = None
    created_at: str | None = None
    updated_at: str | None = None


class MatchReportReferenceLineage(BFFResponseModel):
    algorithm_version: str | None = None
    source_version: str | None = None
    taxonomy_version: str | None = None
    graph_version: str | None = None
    cv_profile_version: str | None = None
    position_profile_version: str | None = None


class MatchReportReferenceResponse(BFFResponseModel):
    evaluation_id: str | None = None
    task_id: str
    resume_id: str | None = None
    position_id: str | None = None
    target_type: str | None = None
    status: BFFReportStatus | None = None
    matching_method: Literal["rule", "semantic_verified", "unknown"] | None = None
    degraded: bool | None = None
    overall_score: float | None = Field(default=None, ge=0, le=100)
    provider: str | None = None
    origin: Literal["manual", "auto_ranking"] | None = None
    error_code: str | None = None
    error_message: str | None = None
    lineage: MatchReportReferenceLineage | None = None
    created_at: str | None = None
    updated_at: str | None = None


class MatchReportExportResponse(BFFResponseModel):
    format: Literal["json"] = "json"
    report: MatchReportResponse


class MatchTaskResultPayloadResponse(BFFResponseModel):
    evaluation_id: str | None = None


class MatchTaskInputPayloadResponse(BFFResponseModel):
    resume_id: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    use_enterprise_weights: bool | None = None
    generate_learning_path: bool | None = None


class MatchTaskLogResponse(BFFResponseModel):
    status: str | None = None
    at: str | None = None
    message: str | None = None


class MatchTaskResponse(BFFResponseModel):
    task_id: str
    task_type: str | None = None
    status: BFFTaskStatus = "pending"
    canonical_status: BFFTaskStatus | None = None
    progress: int | float | None = None
    result_payload: MatchTaskResultPayloadResponse | None = None
    result_reference: str | None = None
    evaluation_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    attempt_count: int | None = Field(default=None, ge=0)
    created_at: str | None = None
    updated_at: str | None = None
    execution_mode: str | None = None
    rule_based: bool | None = None
    provider: str | None = None
    target_type: str | None = None
    use_enterprise_weights: bool | None = None
    generate_learning_path: bool | None = None
    versions: MatchVersionsResponse | None = None
    created: bool | None = None
    implementation_status: str | None = None
    mock: bool | None = None
    algorithm_version: str | None = None
    capability_implementation_status: str | None = None
    input_payload: MatchTaskInputPayloadResponse | None = None
    logs: list[MatchTaskLogResponse] | None = None
    created_by: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class LearningPathResponse(BFFResponseModel):
    path_id: str
    evaluation_id: str
    target_position_id: str | None = None
    time_budget_hours: float | None = None
    learning_goal: str | None = None
    stages: list[LearningStepResponse] = Field(default_factory=list)
    gap_analysis: GapAnalysisResponse = Field(default_factory=GapAnalysisResponse)
    status: Literal["completed", "rejected", "current", "stale", "cancelled"] | None = None
    provider: str | None = None
    algorithm_versions: AlgorithmVersionsResponse | None = None
    data_versions: DataVersionsResponse | None = None
    created_at: str | None = None
    updated_at: str | None = None


class LearningPathExportResponse(BFFResponseModel):
    format: Literal["json"] = "json"
    learning_path: LearningPathResponse


class EligibleResumeResponse(BFFResponseModel):
    resume_id: str
    validated_cv_snapshot_id: str
    skill_count: int = Field(ge=0)
    project_count: int = Field(ge=0)


class CandidateMatchSubmissionItem(BFFResponseModel):
    submission_id: str
    resume_id: str
    status: str
    task_id: str | None = None
    evaluation_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class CandidateMatchSubmissionResponse(BFFResponseModel):
    enterprise_job_id: str
    implementation_status: str
    items: list[CandidateMatchSubmissionItem] = Field(default_factory=list)


class CandidateBoardCoverageResponse(BFFResponseModel):
    matched: int = Field(ge=0)
    total: int = Field(ge=0)
    coverage: float | None = Field(default=None, ge=0, le=1)


class CandidateBoardEvidenceResponse(BFFResponseModel):
    count: int = Field(ge=0)
    samples: list[str] = Field(default_factory=list)


class CandidateBoardStrengthResponse(BFFResponseModel):
    dimension: str
    message: str
    evidence_count: int = Field(ge=0)


class CandidateBoardRiskResponse(BFFResponseModel):
    kind: str
    message: str
    evidence_count: int = Field(ge=0)


class CandidateBoardDecisionResponse(BFFResponseModel):
    decision_id: str
    decision: Literal["fit", "unfit"]
    decided_by: str
    evaluation_id: str | None = None
    task_id: str | None = None
    algorithm_version: str | None = None
    reason_code: str | None = None
    reason_text: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class CandidateBoardEvaluationSnapshotResponse(BFFResponseModel):
    evaluation_id: str
    task_id: str | None = None
    algorithm_version: str | None = None
    evaluated_at: str | None = None
    overall_score: float | None = Field(default=None, ge=0, le=100)
    required_coverage: CandidateBoardCoverageResponse | None = None
    critical_gap_count: int = Field(default=0, ge=0)
    critical_gaps: list[str] = Field(default_factory=list)
    stale_reason_codes: list[str] = Field(default_factory=list)


class CandidateBoardEvaluationDeltaResponse(BFFResponseModel):
    current: CandidateBoardEvaluationSnapshotResponse
    previous: CandidateBoardEvaluationSnapshotResponse
    overall_score_delta: float | None = None
    required_coverage_delta: float | None = None
    critical_gap_count_delta: int = Field(default=0)
    stale_reasons_changed: bool = False


class CandidateBoardItemResponse(BFFResponseModel):
    submission_id: str
    resume_id: str
    candidate_display_name: str
    candidate_status: str
    evaluation_id: str | None = None
    evaluation_status: str
    task_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    overall_score: float | None = Field(default=None, ge=0, le=100)
    match_confidence: float | None = Field(default=None, ge=0, le=1)
    recommendation_level: str | None = None
    stale: bool = False
    required_coverage: CandidateBoardCoverageResponse | None = None
    critical_gap_count: int = Field(default=0, ge=0)
    critical_gaps: list[str] = Field(default_factory=list)
    evidence: CandidateBoardEvidenceResponse | None = None
    strengths: list[CandidateBoardStrengthResponse] = Field(default_factory=list)
    risks: list[CandidateBoardRiskResponse] = Field(default_factory=list)
    rank: int | None = Field(default=None, ge=1)
    decision: CandidateBoardDecisionResponse | None = None
    evaluation_delta: CandidateBoardEvaluationDeltaResponse | None = None


class CandidateBoardResponse(BFFResponseModel):
    enterprise_job_id: str
    total: int = Field(ge=0)
    ranked_count: int = Field(ge=0)
    items: list[CandidateBoardItemResponse] = Field(default_factory=list)


class BFFEnvelope(BFFResponseModel):
    code: Literal[0] = 0
    message: Literal["success"] = "success"
    trace_id: str


class MatchPreflightResponse(BFFResponseModel):
    ready: bool
    cv_snapshot_ready: bool
    cv_profile_ready: bool
    position_profile_ready: bool
    blockers: list[str] = Field(default_factory=list)
    validated_cv_snapshot_id: str | None = None
    position_graph_version: str | None = None


class MatchPreflightEnvelope(BFFEnvelope):
    data: MatchPreflightResponse


class MatchPositionResponse(BFFResponseModel):
    position_id: str
    position_name: str
    taxonomy_family_name: str | None = None
    status: str
    lifecycle_status: str
    matchable: bool
    reason: str
    blockers: list[str] = Field(default_factory=list)
    position_graph_version: str | None = None
    position_profile_version: str | None = None


class MatchPositionListEnvelope(BFFEnvelope):
    data: list[MatchPositionResponse]


class MatchRankingItemResponse(BFFResponseModel):
    rank: int = Field(ge=1)
    position_id: str
    position_name: str
    score: float = Field(ge=0, le=100)
    score_source: Literal["coarse", "formal"]
    calculation_status: Literal["preliminary", "pending", "running", "completed", "failed"]
    evaluation_id: str | None = None
    task_id: str | None = None
    error_code: str | None = None


class MatchRankingResponse(BFFResponseModel):
    resume_id: str
    validated_cv_snapshot_id: str
    algorithm_version: str
    status: Literal["ready", "running", "completed", "cancelled"]
    total: int = Field(ge=0)
    completed: int = Field(ge=0)
    items: list[MatchRankingItemResponse] = Field(default_factory=list)


class MatchRankingEnvelope(BFFEnvelope):
    data: MatchRankingResponse


class CandidateBoardEnvelope(BFFEnvelope):
    data: CandidateBoardResponse


class MatchTaskEnvelope(BFFEnvelope):
    data: MatchTaskResponse


class EligibleResumeListEnvelope(BFFEnvelope):
    data: list[EligibleResumeResponse]


class MatchReportEnvelope(BFFEnvelope):
    data: MatchReportResponse


class MatchReportListEnvelope(BFFEnvelope):
    data: list[MatchReportReferenceResponse]


class MatchReportExportEnvelope(BFFEnvelope):
    data: MatchReportExportResponse


class LearningPathEnvelope(BFFEnvelope):
    data: LearningPathResponse


class LearningPathListEnvelope(BFFEnvelope):
    data: list[LearningPathResponse]


class LearningPathExportEnvelope(BFFEnvelope):
    data: LearningPathExportResponse


class CandidateMatchSubmissionEnvelope(BFFEnvelope):
    data: CandidateMatchSubmissionResponse


class DecisionAuditEnvelope(BFFEnvelope):
    """Recruiter decision audit payload assembled by the enterprise BFF.

    The audit contains versioned metrics and heterogeneous replay cases. Its
    inner contract is generated from persisted evaluation and decision records,
    while the envelope keeps the public response shape strict.
    """

    data: dict[str, object]


class DecisionAuditReplayEnvelope(BFFEnvelope):
    """Single decision-audit case with its formal evaluation replay."""

    data: dict[str, object]


class WhatIfEnvelope(BFFEnvelope):
    data: WhatIfResponse


class EvidenceDeletionEnvelope(BFFEnvelope):
    data: EvidenceDeletionResponse


EvidenceDeletionResponse.model_rebuild()
LearningRouteResponse.model_rebuild()
