"""Focused coverage for B-MATCH-NORM-CAP.

Guarantees the two-level requirement normalization:
* never lets a single requirement exceed ``max_requirement_share``;
* never re-amplifies an already capped requirement to refill total mass;
* records the mass that cannot be allocated as explicit residual mass
  (``allocated_mass + residual_mass == target_scored_mass``);
* keeps the contribution ledger consistent with the formal overall score.
"""

from __future__ import annotations

from app.application.evaluation import MatchEvaluationService
from app.domain.profiles import CVMatchProfile, PositionMatchProfile
from app.domain.scoring import (
    ScoringConfig,
    _ScoreItem,
    _two_level_item_weights,
    build_contribution_ledger,
    score_match_evaluation,
)

EPS = 1e-4


def _item(dimension: str, result_id: str, score: float = 1.0) -> _ScoreItem:
    return _ScoreItem(
        dimension=dimension,
        result_id=result_id,
        status="matched",
        match_type="exact",
        reason_code="TEST",
        score=score,
        confidence=1.0,
        position_evidence=(),
        candidate_evidence=(),
        relation_evidence=(),
    )


def _run(items, effective_weights, target_scored_mass, share=0.4):
    return _two_level_item_weights(
        list(items),
        dict(effective_weights),
        target_scored_mass,
        share,
    )


def _by_requirement(items, weights):
    out: dict[str, float] = {}
    for item, weight in zip(items, weights, strict=True):
        out[item.result_id] = out.get(item.result_id, 0.0) + (weight if item.score is not None else 0.0)
    return out


def test_single_requirement_caps_and_records_residual():
    items = [_item("required_skills", "req-1")]
    weights, status = _run(items, {"required_skills": 1.0}, 1.0, share=0.4)

    assert abs(status.allocated_mass + status.residual_mass - 1.0) < EPS
    assert status.cap_satisfied
    by_req = _by_requirement(items, weights)
    assert abs(by_req["req-1"] - 0.4) < EPS
    assert by_req["req-1"] <= 0.4 + EPS
    assert abs(status.allocated_mass - 0.4) < EPS
    assert abs(status.residual_mass - 0.6) < EPS


def test_two_requirements_both_at_cap_leave_residual():
    items = [
        _item("required_skills", "req-1"),
        _item("required_skills", "req-2"),
    ]
    weights, status = _run(items, {"required_skills": 1.0}, 1.0, share=0.4)

    assert abs(status.allocated_mass + status.residual_mass - 1.0) < EPS
    by_req = _by_requirement(items, weights)
    assert abs(by_req["req-1"] - 0.4) < EPS
    assert abs(by_req["req-2"] - 0.4) < EPS
    assert all(value <= 0.4 + EPS for value in by_req.values())
    assert abs(status.allocated_mass - 0.8) < EPS
    assert abs(status.residual_mass - 0.2) < EPS
    assert status.capped_requirement_count == 2


def test_exactly_feasible_three_requirements_have_no_residual():
    items = [_item("required_skills", f"req-{i}") for i in range(1, 4)]
    weights, status = _run(items, {"required_skills": 1.0}, 1.0, share=0.4)

    by_req = _by_requirement(items, weights)
    assert all(abs(value - 1.0 / 3.0) < EPS for value in by_req.values())
    assert all(value <= 0.4 + EPS for value in by_req.values())
    assert abs(status.residual_mass) < EPS
    assert abs(status.allocated_mass - 1.0) < EPS


def test_n_times_cap_below_one_leaves_residual():
    # 2 requirements * 0.4 cap = 0.8 < 1.0 -> 0.2 cannot be allocated.
    items = [
        _item("required_skills", "req-1"),
        _item("required_skills", "req-2"),
    ]
    weights, status = _run(items, {"required_skills": 1.0}, 1.0, share=0.4)

    by_req = _by_requirement(items, weights)
    assert 2 * 0.4 < 1.0
    assert abs(sum(by_req.values()) - 0.8) < EPS
    assert abs(status.residual_mass - 0.2) < EPS
    assert abs(status.allocated_mass + status.residual_mass - 1.0) < EPS


def test_multi_dimension_cap_enforced_per_requirement():
    items = [
        _item("required_skills", "req-1"),   # 0.6 -> capped 0.4
        _item("responsibilities", "req-2"),  # 0.4 -> at cap
    ]
    weights, status = _run(
        items,
        {"required_skills": 0.6, "responsibilities": 0.4},
        1.0,
        share=0.4,
    )

    by_req = _by_requirement(items, weights)
    assert abs(by_req["req-1"] - 0.4) < EPS
    assert abs(by_req["req-2"] - 0.4) < EPS
    assert all(value <= 0.4 + EPS for value in by_req.values())
    assert abs(status.allocated_mass - 0.8) < EPS
    assert abs(status.residual_mass - 0.2) < EPS


def test_duplicate_result_id_across_dimensions_shares_one_cap_budget():
    # A required skill emits both a required_skills() and a capability_level()
    # item under the same result_id: their combined share must respect the cap.
    items = [
        _item("required_skills", "req-same"),
        _item("capability_level", "req-same"),
    ]
    weights, status = _run(
        items,
        {"required_skills": 0.6, "capability_level": 0.4},
        1.0,
        share=0.4,
    )

    by_req = _by_requirement(items, weights)
    assert abs(by_req["req-same"] - 0.4) < EPS
    assert by_req["req-same"] <= 0.4 + EPS
    assert all(weight <= 0.4 + EPS for weight in weights)
    assert abs(status.allocated_mass + status.residual_mass - 1.0) < EPS


def test_all_requirements_saturated_reports_residual():
    items = [
        _item("required_skills", "req-1"),
        _item("required_skills", "req-2"),
        _item("required_skills", "req-3"),
        _item("required_skills", "req-4"),
    ]
    weights, status = _run(items, {"required_skills": 1.0}, 1.0, share=0.25)

    by_req = _by_requirement(items, weights)
    assert all(abs(value - 0.25) < EPS for value in by_req.values())
    assert abs(status.allocated_mass + status.residual_mass - 1.0) < EPS
    assert abs(status.residual_mass) < EPS
    # With 4 requirements * 0.25 exactly saturating, no residual and no overshoot.
    assert status.cap_satisfied

    items_two = [_item("required_skills", f"req-{i}") for i in range(1, 3)]
    _, status_two = _run(items_two, {"required_skills": 1.0}, 1.0, share=0.25)
    assert abs(status_two.residual_mass - 0.5) < EPS


def _models(cv_payload: dict, position_payload: dict):
    return (
        CVMatchProfile.model_validate(cv_payload),
        PositionMatchProfile.model_validate(position_payload),
    )


def test_end_to_end_ledger_consistent_with_cap_and_residual(
    ready_cv_json, ready_position_json
):
    evaluator = MatchEvaluationService()
    evaluation = evaluator.evaluate(
        {
            "cv_profile": ready_cv_json,
            "position_profile": ready_position_json,
        },
        include_semantic=False,
    )
    cv, position = _models(ready_cv_json, ready_position_json)
    config = ScoringConfig(
        two_level_requirement_normalization=True,
        max_requirement_share=0.10,
    )
    final = score_match_evaluation(evaluation, cv, position, config)

    status = final.two_level_normalization
    assert status is not None
    assert status.cap_satisfied
    assert abs(status.allocated_mass + status.residual_mass - status.target_scored_mass) < 1e-3
    # every recorded requirement share must respect the cap
    assert all(
        share.allocated_weight <= status.max_requirement_share + EPS
        for share in status.requirement_shares
    )
    # version bump leaves the legacy version number behind
    assert final.algorithm_version.endswith("-two-level.v2")
    assert final.scoring_config_version.endswith("-two-level.v2")

    ledger = build_contribution_ledger(evaluation, cv, position, config)
    assert ledger.two_level_normalization is not None
    assert abs(ledger.overall_score - final.overall_score) < EPS
    assert abs(ledger.weighted_points_sum() - (final.overall_score or 0.0)) < 0.02
