"""Immutable contracts for deterministic gap analysis and learning paths."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.domain.evaluation import DimensionScore
from app.domain.profiles import Evidence, ImmutableDTO
from app.domain.what_if import DimensionDelta, WhatIfAction

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
CostSourceType = Literal[
    "dataset_backed", "expert_estimate", "manual", "heuristic", "unknown"
]
EstimateStatus = Literal["verified", "estimated", "unknown"]


class ProfileReferences(ImmutableDTO):
    cv_profile_id: str | None = Field(default=None, min_length=1)
    cv_profile_version: str | None = Field(default=None, min_length=1)
    position_profile_id: str | None = Field(default=None, min_length=1)
    position_profile_version: str | None = Field(default=None, min_length=1)


class PrioritizedGap(ImmutableDTO):
    gap_type: GapType
    requirement_id: str = Field(min_length=1)
    skill_id: str | None = None
    current_level: str | None = None
    target_level: str | None = None
    priority: GapPriority
    priority_score: float = Field(ge=0, le=100)
    reason_codes: tuple[str, ...] = Field(min_length=1)
    evidence: tuple[Evidence, ...] = ()
    # Evidence-side origin: false means the position requirement itself cannot
    # be grounded, so a candidate-side learning action must not be generated.
    position_evidence_present: bool = False
    candidate_evidence_present: bool = False
    source_match_type: str | None = None
    transferable_skill_ids: tuple[str, ...] = ()
    transferability_score: float = Field(default=0.0, ge=0, le=1)
    prerequisite_skill_ids: tuple[str, ...] = ()
    current_ownership: str | None = None
    target_ownership: str | None = None
    score_effect_status: Literal["modeled", "not_modeled_in_v1"] = "modeled"


class CounterfactualSuggestion(ImmutableDTO):
    requirement_id: str = Field(min_length=1)
    skill_id: str | None = None
    suggestion: str = Field(min_length=1)
    basis_evidence: tuple[Evidence, ...] = ()


class PrerequisiteState(ImmutableDTO):
    skill_id: str = Field(min_length=1)
    status: Literal["satisfied", "missing", "unknown"]
    source: Literal["candidate_profile", "evaluation", "unavailable"]
    evidence_refs: tuple[Evidence, ...] = ()


class LearningStep(ImmutableDTO):
    step_order: int = Field(ge=1)
    source_action_id: str | None = None
    target_skill_id: str | None = None
    objective: str = Field(min_length=1)
    prerequisite_skill_ids: tuple[str, ...] = ()
    basis: tuple[str, ...] = Field(min_length=1)
    estimated_hours: float = Field(ge=0)
    cost_source_type: CostSourceType = "unknown"
    cost_source_ref: str | None = None
    estimate_status: EstimateStatus = "unknown"
    cost_model: str = Field(default="gap-learning-hours.v1", min_length=1)
    completion_criteria: tuple[str, ...] = Field(min_length=1)
    source_requirement_ids: tuple[str, ...] = Field(min_length=1)
    reason_codes: tuple[str, ...] = Field(min_length=1)
    prerequisite_states: tuple[PrerequisiteState, ...] = ()
    planning_status: Literal["ready", "blocked"] = "ready"
    blocked_reason_codes: tuple[str, ...] = ()


class SkillPathEdge(ImmutableDTO):
    relation_id: str = Field(min_length=1)
    source_skill_id: str = Field(min_length=1)
    target_skill_id: str = Field(min_length=1)
    relation_type: Literal["equivalent", "parent_child", "related", "transferable"]
    graph_version: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    hop_number: int = Field(ge=1, le=2)
    edge_cost_hours: float = Field(ge=0)
    evidence_refs: tuple[Evidence, ...] = Field(min_length=1)
    score_credit_allowed: bool = False


class SkillTransferPath(ImmutableDTO):
    path_id: str = Field(min_length=1)
    source_skill_id: str = Field(min_length=1)
    target_skill_id: str = Field(min_length=1)
    target_requirement_id: str = Field(min_length=1)
    node_skill_ids: tuple[str, ...] = Field(min_length=2)
    edges: tuple[SkillPathEdge, ...] = Field(min_length=1, max_length=2)
    hop_count: int = Field(ge=1, le=2)
    total_cost_hours: float = Field(ge=0)
    minimum_confidence: float = Field(ge=0, le=1)
    effective_confidence: float = Field(ge=0, le=1)
    outcome_status: Literal["eligible", "partial"]
    graph_version_id: str = Field(min_length=1)
    cost_model: Literal["heuristic_transfer_path.v1"] = "heuristic_transfer_path.v1"
    score_credit_allowed: bool = False
    suitable_for_learning: bool = True


class SkillPathDecision(ImmutableDTO):
    target_requirement_id: str = Field(min_length=1)
    target_skill_id: str = Field(min_length=1)
    status: Literal["reachable", "unreachable"]
    paths: tuple[SkillTransferPath, ...] = ()
    reason_codes: tuple[str, ...] = ()
    max_hops: int = Field(ge=1, le=2)
    max_cost_hours: float = Field(ge=0)
    relation_whitelist: tuple[str, ...] = Field(min_length=1)
    source_status: Literal["available", "unavailable", "error"]
    algorithm_version: Literal["controlled-skill-path.v1"] = "controlled-skill-path.v1"


class LearningRoute(ImmutableDTO):
    # Outcome boundary: routes are *modeled counterfactual re-scores* of the
    # candidate's hypothetical profile, not observed real-world learning gains.
    outcome_semantics: Literal["modeled_counterfactual"] = "modeled_counterfactual"
    observed_outcome: Literal[False] = False

    route_type: Literal["fastest_employment", "budget_max_gain", "foundation_first"]
    action_ids: tuple[str, ...] = Field(min_length=1)
    total_cost_hours: float = Field(ge=0)
    baseline_score: float | None = Field(default=None, ge=0, le=100)
    # Primary modeled-contract fields.
    modeled_final_score: float | None = Field(default=None, ge=0, le=100)
    modeled_score_delta: float | None = None
    modeled_confidence_delta: float | None = None
    # Deprecated aliases (same values) kept for compatibility.
    final_score: float | None = Field(default=None, ge=0, le=100)
    projected_match_gain: float | None = None
    confidence_gain: float | None = None
    target_reachable: bool
    final_recommendation: str | None = None
    remaining_blocker_ids: tuple[str, ...] = ()
    path_refs: tuple[str, ...] = ()
    # Explicit per-route cost provenance (real actions of this route). Kept
    # optional/empty for backward compatibility with older payloads.
    action_costs: tuple[ActionCost, ...] = ()
    # Modeled scenario dimension scores for the radar comparison panel. Kept
    # empty for older payloads; the baseline panel reuses the report scores.
    scenario_dimension_scores: tuple[DimensionScore, ...] = ()
    algorithm_version: Literal[
        "learning-route-enumeration.v1", "learning-route-enumeration.v2"
    ] = "learning-route-enumeration.v2"


class ActionCost(ImmutableDTO):
    action_id: str = Field(min_length=1)
    direct_hours: float = Field(ge=0)
    dependency_hours: float = Field(ge=0)
    total_hours: float = Field(ge=0)
    min_hours: float = Field(ge=0)
    max_hours: float = Field(ge=0)
    cost_confidence: float = Field(ge=0, le=1)
    difficulty: Literal["low", "medium", "high"]
    selected: bool
    cost_model: str = Field(min_length=1)
    cost_source_type: CostSourceType = "unknown"
    cost_source_ref: str | None = None
    estimate_status: EstimateStatus = "unknown"


class MinimalActionSet(ImmutableDTO):
    # Outcome boundary: this is a modeled counterfactual re-score, not an
    # observed real-world outcome after the candidate completes the actions.
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
    source_evaluation_id: str = Field(min_length=1)
    scenario_id: str | None = None
    selected_action_ids: tuple[str, ...] = ()
    deferred_action_ids: tuple[str, ...] = ()
    action_costs: tuple[ActionCost, ...] = ()
    minimum_action_count: int = Field(ge=0)
    total_cost_hours: float = Field(ge=0)
    budget_hours: float | None = Field(default=None, ge=0)
    budget_used_hours: float = Field(ge=0)
    budget_remaining_hours: float | None = Field(default=None, ge=0)
    baseline_score: float | None = Field(default=None, ge=0, le=100)
    # Primary modeled-contract fields.
    modeled_final_score: float | None = Field(default=None, ge=0, le=100)
    modeled_score_delta: float | None = None
    modeled_confidence_delta: float | None = None
    # Deprecated aliases (same values) kept for compatibility.
    scenario_score: float | None = Field(default=None, ge=0, le=100)
    score_delta: float | None = None
    dimension_deltas: tuple[DimensionDelta, ...] = ()
    baseline_hard_gate_status: str | None = None
    scenario_hard_gate_status: str | None = None
    hard_gate_delta: str | None = None
    target_reachable: bool
    covered_requirement_ids: tuple[str, ...] = ()
    evidence_refs: tuple[Evidence, ...] = ()
    path_refs: tuple[str, ...] = ()
    unreachable_reason_codes: tuple[str, ...] = ()
    cv_profile_version: str | None = None
    position_profile_version: str | None = None
    graph_version_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    search_status: Literal["exact_bounded", "bounded_beam"] = "exact_bounded"
    projected_if_completed: bool = False
    algorithm_version: Literal[
        "minimal-action-set.v1",
        "minimal-action-set.v2",
        "minimal-action-set.v3",
    ] = "minimal-action-set.v3"


class GapAnalysis(ImmutableDTO):
    generation_status: Literal["completed", "rejected"]
    prioritized_gaps: tuple[PrioritizedGap, ...] = ()
    learning_path: tuple[LearningStep, ...] = ()
    counterfactual_suggestions: tuple[CounterfactualSuggestion, ...] = ()
    candidate_actions: tuple[WhatIfAction, ...] = ()
    learning_routes: tuple[LearningRoute, ...] = ()
    minimal_action_set: MinimalActionSet | None = None
    skill_path_decisions: tuple[SkillPathDecision, ...] = ()
    time_budget_hours: float | None = Field(default=None, ge=0)
    over_budget: bool = False
    estimated_readiness: float | None = Field(default=None, ge=0, le=1)
    profile_references: ProfileReferences
    algorithm_version: str = Field(min_length=1)
    config_version: str = Field(min_length=1)
    gap_policy_version: str = Field(default="", min_length=1)
    gap_policy_hash: str = Field(default="", min_length=1)
    source_evaluation_algorithm_version: str | None = None
    source_scoring_algorithm_version: str | None = None
    source_scoring_config_version: str | None = None
    semantic_algorithm_version: str | None = None
    embedding_version: str | None = None
    error_code: str | None = None
    error_message: str | None = None
