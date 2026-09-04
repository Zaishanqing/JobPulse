"""Contracts for deterministic explanation Evidence deletion tests."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.domain.evaluation import MatchEvaluation
from app.domain.gaps import GapAnalysis
from app.domain.profiles import ImmutableDTO
from app.domain.what_if import DimensionDelta


class ExplanationFactor(ImmutableDTO):
    factor_id: str = Field(min_length=1)
    factor_type: Literal[
        "hard_constraint",
        "required_skill",
        "preferred_skill",
        "responsibility",
        "project",
        "scenario",
        "unused_evidence",
    ]
    requirement_id: str | None = None
    reason_code: str = Field(min_length=1)
    criticality: Literal["critical", "noncritical"]
    evidence_source_ids: tuple[str, ...] = ()
    used_by_scorer: bool
    evidence_supported: bool


class FeatureAblationCertificate(ImmutableDTO):
    status: Literal["ablated", "noop"]
    profile_fingerprint_changed: bool
    input_closure_removed: bool
    candidate_evidence_removed: bool
    residual_trace_refs: tuple[str, ...] = ()
    reason: str


class FeatureContributionGroup(ImmutableDTO):
    canonical_feature_id: str
    canonical_feature: str
    member_requirement_ids: tuple[str, ...]
    dimensions: tuple[str, ...]
    baseline_weighted_points: float
    evidence_source_ids: tuple[str, ...]
    confidence: float = 0.0


class EvidenceDeletionResult(ImmutableDTO):
    generation_status: Literal["completed", "rejected"]
    deletion_run_id: str = Field(min_length=1)
    deletion_kind: Literal["critical", "noncritical"] | None = None
    deleted_evidence_source_ids: tuple[str, ...] = ()
    critical_evidence_source_ids: tuple[str, ...] = ()
    noncritical_evidence_source_ids: tuple[str, ...] = ()
    explanation_factors: tuple[ExplanationFactor, ...] = ()
    baseline_evaluation: MatchEvaluation | None = None
    ablated_evaluation: MatchEvaluation | None = None
    baseline_gap_analysis: GapAnalysis | None = None
    ablated_gap_analysis: GapAnalysis | None = None
    baseline_score: float | None = Field(default=None, ge=0, le=100)
    ablated_score: float | None = Field(default=None, ge=0, le=100)
    retained_only_score: float | None = Field(default=None, ge=0, le=100)
    score_delta: float | None = None
    dimension_deltas: tuple[DimensionDelta, ...] = ()
    baseline_hard_gate_status: str | None = None
    ablated_hard_gate_status: str | None = None
    hard_gate_delta: str | None = None
    added_gap_ids: tuple[str, ...] = ()
    removed_gap_ids: tuple[str, ...] = ()
    added_action_ids: tuple[str, ...] = ()
    removed_action_ids: tuple[str, ...] = ()
    comprehensiveness: float | None = Field(default=None, ge=0)
    sufficiency: float | None = Field(default=None, ge=0)
    unsupported_reason_rate: float = Field(default=0.0, ge=0, le=1)
    faithfulness_status: Literal[
        "faithful", "possibly_unfaithful", "unstable", "not_applicable"
    ] = "not_applicable"
    baseline_evaluation_id: str | None = None
    cv_profile_version: str | None = None
    position_profile_version: str | None = None
    scoring_algorithm_version: str | None = None
    scoring_config_version: str | None = None
    classification_policy_version: Literal[
        "explanation-factor-policy.v1",
        "contribution-ledger.v2",
    ] = "explanation-factor-policy.v1"
    stability_threshold_points: float = Field(default=1.0, ge=0)
    hypothetical: Literal[True] = True
    algorithm_version: Literal[
        "evidence-deletion-recompute.v1",
        "evidence-deletion-recompute.v2",
    ] = "evidence-deletion-recompute.v1"
    error_code: str | None = None
    error_message: str | None = None
