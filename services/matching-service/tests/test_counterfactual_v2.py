"""Tests for the contribution ledger and the counterfactual v2 engine."""

from __future__ import annotations

from app.application.evaluation import MatchEvaluationService
from app.application.explanation_deletion import ExplanationDeletionService
from app.application.learning_paths import LearningPathService
from app.application.what_if import WhatIfService
from app.domain.counterfactual import CounterfactualContributionEngine
from app.domain.profiles import CVMatchProfile, PositionMatchProfile
from app.domain.scoring import (
    ScoringConfig,
    build_contribution_ledger,
)


def _models(cv_payload: dict, position_payload: dict):
    return (
        CVMatchProfile.model_validate(cv_payload),
        PositionMatchProfile.model_validate(position_payload),
    )


def _baseline(ready_cv_json, ready_position_json):
    evaluator = MatchEvaluationService()
    evaluation = evaluator.evaluate(
        {
            "cv_profile": ready_cv_json,
            "position_profile": ready_position_json,
        },
        include_semantic=False,
    )
    assert evaluation.evaluation_status == "completed"
    cv, position = _models(ready_cv_json, ready_position_json)
    return evaluation, cv, position


def test_contribution_ledger_matches_formal_overall_score(
    ready_cv_json, ready_position_json
) -> None:
    evaluation, cv, position = _baseline(ready_cv_json, ready_position_json)
    ledger = build_contribution_ledger(evaluation, cv, position)
    final = evaluation.final_match_result
    assert final is not None
    assert ledger.overall_score == final.overall_score
    assert abs(ledger.weighted_points_sum() - (final.overall_score or 0.0)) < 0.02


def test_candidate_actions_only_target_unmet_requirements(
    ready_cv_json, ready_position_json
) -> None:
    evaluation, cv, position = _baseline(ready_cv_json, ready_position_json)
    ledger = build_contribution_ledger(evaluation, cv, position)
    engine = CounterfactualContributionEngine()
    actions = engine.candidate_actions(ledger)
    assert actions
    assert all(action.expected_score_delta > 0 for action in actions)
    action_ids = [item.action_id for item in actions]
    assert len(action_ids) == len(set(action_ids))


def test_expected_delta_matches_actual_recompute_for_added_skill(
    ready_cv_json, ready_position_json
) -> None:
    evaluation, cv, position = _baseline(ready_cv_json, ready_position_json)
    ledger = build_contribution_ledger(evaluation, cv, position)
    engine = CounterfactualContributionEngine()
    actions = engine.top_k_actions(ledger)
    assert actions
    action = actions[0]
    action_payload = {
        "action_id": action.action_id,
        "action_type": action.action_type,
        "skill_id": action.canonical_feature_id,
        "canonical_name": action.canonical_feature,
        "target_level": action.target_level,
            "target_requirement_ids": (action.requirement_id,),
            "estimated_hours": action.estimated_hours,
            "milestone_status": "verified",
        }
    result = WhatIfService(MatchEvaluationService()).evaluate(
        {
            "cv_profile": ready_cv_json,
            "position_profile": ready_position_json,
            "baseline_evaluation": evaluation.model_dump(mode="python"),
            "actions": [action_payload],
        }
    )
    assert result.generation_status == "completed"
    assert result.score_delta is not None
    assert result.score_delta > 0
    assert abs(result.score_delta - action.expected_score_delta) < 0.5


def test_critical_factor_deletion_drops_score_and_noncritical_does_not(
    ready_cv_json, ready_position_json
) -> None:
    evaluation, cv, position = _baseline(ready_cv_json, ready_position_json)
    ledger = build_contribution_ledger(evaluation, cv, position)
    engine = CounterfactualContributionEngine()
    critical, noncritical = engine.classify_factors(ledger)
    assert critical
    assert noncritical
    service = ExplanationDeletionService(
        MatchEvaluationService(), LearningPathService()
    )
    payload = {
        "cv_profile": ready_cv_json,
        "position_profile": ready_position_json,
        "baseline_evaluation": evaluation.model_dump(mode="python"),
    }
    critical_result = service.evaluate_contribution_v2(
        {**payload, "deletion_kind": "critical"}
    )
    noncritical_result = service.evaluate_contribution_v2(
        {**payload, "deletion_kind": "noncritical"}
    )
    assert critical_result.generation_status == "completed"
    assert noncritical_result.generation_status == "completed"
    assert critical_result.score_delta is not None
    assert critical_result.score_delta < 0
    assert noncritical_result.score_delta == 0.0


def test_faithfulness_separation_uses_real_deltas(
    ready_cv_json, ready_position_json
) -> None:
    evaluation, cv, position = _baseline(ready_cv_json, ready_position_json)
    engine = CounterfactualContributionEngine()
    service = ExplanationDeletionService(
        MatchEvaluationService(), LearningPathService()
    )
    payload = {
        "cv_profile": ready_cv_json,
        "position_profile": ready_position_json,
        "baseline_evaluation": evaluation.model_dump(mode="python"),
    }
    critical_result = service.evaluate_contribution_v2(
        {**payload, "deletion_kind": "critical"}
    )
    noncritical_result = service.evaluate_contribution_v2(
        {**payload, "deletion_kind": "noncritical"}
    )
    separation = engine.faithfulness_separation(
        critical_delta=critical_result.score_delta,
        noncritical_delta=noncritical_result.score_delta,
    )
    assert separation is not None
    assert separation < 0


def test_two_level_normalization_caps_single_requirement_share(
    ready_cv_json, ready_position_json
) -> None:
    evaluation, cv, position = _baseline(ready_cv_json, ready_position_json)
    legacy = build_contribution_ledger(evaluation, cv, position)
    two_level = build_contribution_ledger(
        evaluation,
        cv,
        position,
        ScoringConfig(two_level_requirement_normalization=True),
    )
    legacy_top = max(
        item.weighted_points for item in legacy.requirement_contributions
    )
    two_top = max(
        item.weighted_points for item in two_level.requirement_contributions
    )
    assert two_top < legacy_top
    assert abs(two_level.weighted_points_sum() - (two_level.overall_score or 0)) < 0.02


def test_two_level_normalization_keeps_total_weight_constant(
    ready_cv_json, ready_position_json
) -> None:
    evaluation, cv, position = _baseline(ready_cv_json, ready_position_json)
    legacy = build_contribution_ledger(evaluation, cv, position)
    two_level = build_contribution_ledger(
        evaluation,
        cv,
        position,
        ScoringConfig(two_level_requirement_normalization=True),
    )
    legacy_weight = sum(
        item.weight for item in legacy.requirement_contributions
    )
    two_weight = sum(
        item.weight for item in two_level.requirement_contributions
    )
    assert abs(legacy_weight - two_weight) < 0.02
