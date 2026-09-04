"""Unit tests for counterfactual-planner.v3 pure ranking/selection logic.

The evaluation callback receives a normalized action-set tuple, so greedy
minimal-set search is backed by real ``f(selected + [candidate])`` deltas
instead of single-action outcomes.  ``minimal_action_set`` returns a formal
``MinimalActionSetResult`` whose ``final_outcome`` is the final set outcome.
"""

from __future__ import annotations

from app.domain.counterfactual_planner import (
    CounterfactualPlannerV3,
    LookaheadOutcome,
)


def _planner(
    outcomes: dict[tuple[str, ...], LookaheadOutcome],
    **overrides,
) -> CounterfactualPlannerV3:
    return CounterfactualPlannerV3(
        lambda action_ids: outcomes[tuple(action_ids)],
        baseline_score=60.0,
        baseline_hard_gate_status="passed",
        baseline_recommendation="weak_match",
        **overrides,
    )


def _outcome(
    action_id: str,
    *,
    score_delta: float = 1.0,
    gate_delta: int = 0,
    recommendation_delta: int = 0,
    confidence_delta: float = 0.1,
    effort: float = 8.0,
    recommendation: str = "weak_match",
    scenario_gate: str = "passed",
) -> LookaheadOutcome:
    return LookaheadOutcome(
        action_id=action_id,
        score_delta=score_delta,
        gate_rank_delta=gate_delta,
        recommendation_rank_delta=recommendation_delta,
        confidence_delta=confidence_delta,
        effort=effort,
        scenario_score=60.0 + score_delta,
        scenario_hard_gate_status=scenario_gate,
        scenario_recommendation=recommendation,
    )


def test_rank_uses_lexicographic_objective() -> None:
    outcomes = {
        ("gate-fix",): _outcome(
            "gate-fix",
            score_delta=0.2,
            gate_delta=2,
            recommendation_delta=1,
            effort=20.0,
        ),
        ("high-score",): _outcome("high-score", score_delta=10.0, effort=4.0),
        ("low-effort",): _outcome("low-effort", score_delta=5.0, effort=1.0),
        ("high-confidence",): _outcome(
            "high-confidence", score_delta=5.0, confidence_delta=0.5, effort=2.0
        ),
    }
    planner = _planner(outcomes)
    ranked = planner.rank([key[0] for key in outcomes])
    assert [item.action_id for item in ranked] == [
        "gate-fix",
        "high-score",
        "high-confidence",
        "low-effort",
    ]


def test_minimal_action_set_greedy_and_target_stop() -> None:
    outcomes = {
        ("python",): _outcome(
            "python",
            score_delta=12.0,
            recommendation_delta=1,
            recommendation="potential_match",
            effort=8.0,
        ),
        ("go",): _outcome(
            "go",
            score_delta=9.0,
            recommendation_delta=1,
            recommendation="potential_match",
            effort=8.0,
        ),
        ("project",): _outcome(
            "project",
            score_delta=3.0,
            recommendation_delta=0,
            recommendation="weak_match",
            effort=6.0,
        ),
        ("go", "python"): _outcome(
            "python",
            score_delta=16.0,
            recommendation_delta=2,
            recommendation="strong_match",
            effort=16.0,
        ),
        ("project", "python"): _outcome(
            "project",
            score_delta=13.0,
            recommendation_delta=1,
            recommendation="potential_match",
            effort=14.0,
        ),
    }
    planner = _planner(outcomes, marginal_delta_epsilon=0.05)
    result = planner.minimal_action_set(
        ["python", "go", "project"],
        target_recommendation="strong_match",
    )
    assert result.reached_target is True
    assert result.stop_reason == "target_reached"
    assert [step.action_id for step in result.steps] == ["python", "go"]
    assert result.selected_action_ids == ("python", "go")


def test_minimal_action_set_stops_when_no_marginal_improvement() -> None:
    outcomes = {
        ("a",): _outcome("a", score_delta=0.5, recommendation_delta=0),
        ("b",): _outcome("b", score_delta=0.4, recommendation_delta=0),
    }
    planner = _planner(outcomes, marginal_delta_epsilon=0.95)
    result = planner.minimal_action_set(
        ["a", "b"],
        target_recommendation="strong_match",
    )
    assert not result.steps
    assert result.reached_target is False
    assert result.stop_reason == "no_improvement"


def test_minimal_action_set_respects_max_actions() -> None:
    singles = {
        f"action-{index}": _outcome(
            f"action-{index}",
            score_delta=2.0,
            recommendation_delta=1,
            recommendation="potential_match",
        )
        for index in range(6)
    }

    def callback(action_ids: tuple[str, ...]) -> LookaheadOutcome:
        if len(action_ids) == 1:
            return singles[action_ids[0]]
        base = singles[action_ids[-1]]
        return _outcome(
            action_ids[-1],
            score_delta=base.score_delta + len(action_ids) - 1,
            recommendation_delta=1,
            recommendation="potential_match",
            effort=base.effort * len(action_ids),
        )

    planner = CounterfactualPlannerV3(
        callback,
        baseline_score=60.0,
        baseline_hard_gate_status="passed",
        baseline_recommendation="weak_match",
        max_actions=3,
    )
    result = planner.minimal_action_set(
        [f"action-{index}" for index in range(6)],
        target_recommendation="strong_match",
    )
    assert len(result.steps) <= 3
    assert result.stop_reason == "action_cap"


def test_baseline_already_at_target_returns_empty_set() -> None:
    planner = CounterfactualPlannerV3(
        lambda _action_ids: _outcome("unused", recommendation="strong_match"),
        baseline_score=90.0,
        baseline_hard_gate_status="passed",
        baseline_recommendation="strong_match",
    )
    result = planner.minimal_action_set(["a", "b"])
    assert result.selected_action_ids == ()
    assert result.steps == ()
    assert result.reached_target is True
    assert result.stop_reason == "baseline_already_at_target"


def test_minimal_set_discovers_synergy_pair_and_final_outcome_is_set_level() -> None:
    outcomes = {
        ("a",): _outcome("a", score_delta=1.0, recommendation_delta=0),
        ("b",): _outcome("b", score_delta=0.5, recommendation_delta=0),
        ("a", "b"): _outcome(
            "b",
            score_delta=3.0,
            recommendation_delta=2,
            recommendation="strong_match",
            effort=16.0,
        ),
    }
    planner = _planner(outcomes)
    result = planner.minimal_action_set(
        ["a", "b"],
        target_recommendation="strong_match",
    )
    assert [step.action_id for step in result.steps] == ["a", "b"]
    assert result.steps[1].marginal_score_delta == 2.0
    assert result.final_outcome is not None
    assert result.final_outcome.score_delta == 3.0


def test_minimal_set_stops_at_redundant_overlap() -> None:
    outcomes = {
        ("a",): _outcome(
            "a",
            score_delta=10.0,
            recommendation_delta=2,
            recommendation="strong_match",
        ),
        ("b",): _outcome(
            "b",
            score_delta=8.0,
            recommendation_delta=2,
            recommendation="strong_match",
        ),
        ("a", "b"): _outcome(
            "b",
            score_delta=10.0,
            recommendation_delta=2,
            recommendation="strong_match",
            effort=16.0,
        ),
    }
    planner = _planner(outcomes)
    result = planner.minimal_action_set(
        ["a", "b"],
        target_recommendation="strong_match",
    )
    assert [step.action_id for step in result.steps] == ["a"]
    assert result.reached_target is True


def test_minimal_set_combines_hard_gate_and_soft_score() -> None:
    outcomes = {
        ("gate",): _outcome(
            "gate",
            score_delta=0.5,
            gate_delta=2,
            recommendation_delta=1,
            recommendation="potential_match",
            scenario_gate="passed",
            effort=20.0,
        ),
        ("score",): _outcome(
            "score",
            score_delta=5.0,
            recommendation_delta=0,
            recommendation="weak_match",
            effort=4.0,
        ),
        ("gate", "score"): _outcome(
            "score",
            score_delta=5.5,
            gate_delta=2,
            recommendation_delta=2,
            recommendation="strong_match",
            effort=24.0,
        ),
    }
    planner = _planner(outcomes)
    result = planner.minimal_action_set(
        ["gate", "score"],
        target_recommendation="strong_match",
    )
    assert [step.action_id for step in result.steps] == ["gate", "score"]
    assert result.steps[1].marginal_score_delta == 5.0
    assert result.steps[1].marginal_gate_rank_delta == 0
    assert result.steps[1].marginal_recommendation_rank_delta == 1


def test_minimal_set_keeps_conflicting_pair_when_recommendation_improves() -> None:
    outcomes = {
        ("a",): _outcome(
            "a",
            score_delta=9.0,
            recommendation_delta=1,
            recommendation="potential_match",
        ),
        ("b",): _outcome(
            "b",
            score_delta=9.0,
            recommendation_delta=1,
            recommendation="potential_match",
        ),
        ("a", "b"): _outcome(
            "b",
            score_delta=4.0,
            recommendation_delta=2,
            recommendation="strong_match",
            effort=16.0,
        ),
    }
    planner = _planner(outcomes)
    result = planner.minimal_action_set(
        ["a", "b"],
        target_recommendation="strong_match",
    )
    assert [step.action_id for step in result.steps] == ["a", "b"]
    assert result.steps[1].marginal_score_delta == -5.0
    assert result.steps[1].cumulative_score_delta == 4.0


def test_minimal_set_evaluates_full_combination_not_singleton() -> None:
    calls: list[tuple[str, ...]] = []

    def callback(action_ids: tuple[str, ...]) -> LookaheadOutcome:
        calls.append(tuple(action_ids))
        combination = tuple(sorted(action_ids))
        if combination == ("a", "b"):
            return _outcome(
                "b",
                score_delta=3.0,
                recommendation_delta=2,
                recommendation="strong_match",
                effort=16.0,
            )
        action_id = action_ids[0]
        return _outcome(
            action_id,
            score_delta=1.0 if action_id == "a" else 0.5,
            recommendation_delta=0,
        )

    planner = CounterfactualPlannerV3(
        callback,
        baseline_score=60.0,
        baseline_hard_gate_status="passed",
        baseline_recommendation="weak_match",
    )
    result = planner.minimal_action_set(
        ["a", "b"],
        target_recommendation="strong_match",
    )
    assert ("a", "b") in calls
    assert len(result.steps) == 2
    assert result.steps[1].marginal_score_delta == 2.0
