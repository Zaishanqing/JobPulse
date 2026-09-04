"""Tests for the exhaustive formal-scorer oracle of MATCH v4."""

from __future__ import annotations

from app.domain.counterfactual_oracle import (
    exhaustive_optimal_action_set,
    regret_report,
)
from app.domain.counterfactual_planner import LookaheadOutcome


def _outcome(
    *,
    score_delta: float,
    recommendation: str,
    confidence_delta: float = 0.1,
) -> LookaheadOutcome:
    return LookaheadOutcome(
        action_id="",
        score_delta=score_delta,
        gate_rank_delta=0,
        recommendation_rank_delta=0,
        confidence_delta=confidence_delta,
        effort=8.0,
        scenario_score=60.0 + score_delta,
        scenario_hard_gate_status="passed",
        scenario_recommendation=recommendation,
    )


def _evaluator(outcomes: dict[tuple[str, ...], LookaheadOutcome]):
    def evaluate(action_ids: tuple[str, ...]) -> LookaheadOutcome:
        return outcomes[tuple(sorted(action_ids))]

    return evaluate


def test_oracle_finds_synergy_pair_and_reports_exact_greedy() -> None:
    outcomes = {
        ("a",): _outcome(score_delta=1.0, recommendation="weak_match"),
        ("b",): _outcome(score_delta=0.5, recommendation="weak_match"),
        ("a", "b"): _outcome(
            score_delta=3.0, recommendation="strong_match"
        ),
    }
    optimal = exhaustive_optimal_action_set(
        _evaluator(outcomes), ["a", "b"]
    )
    assert optimal == ("a", "b")
    report = regret_report(
        greedy_ids=["a", "b"],
        greedy_outcome=outcomes[("a", "b")],
        optimal_ids=optimal,
        optimal_outcome=outcomes[("a", "b")],
    )
    assert report["exact_optimal_set"] is True
    assert report["set_size_regret"] == 0
    assert report["score_regret"] == 0
    assert report["recommendation_success_regret"] == 0


def test_oracle_prefers_smallest_successful_subset() -> None:
    outcomes = {
        ("a",): _outcome(score_delta=9.0, recommendation="strong_match"),
        ("b",): _outcome(score_delta=8.0, recommendation="strong_match"),
        ("a", "b"): _outcome(
            score_delta=10.0, recommendation="strong_match"
        ),
    }
    optimal = exhaustive_optimal_action_set(
        _evaluator(outcomes), ["a", "b"]
    )
    assert optimal == ("a",)
    report = regret_report(
        greedy_ids=["a", "b"],
        greedy_outcome=outcomes[("a", "b")],
        optimal_ids=optimal,
        optimal_outcome=outcomes[("a",)],
    )
    assert report["exact_optimal_set"] is False
    assert report["set_size_regret"] == 1
    assert report["score_regret"] == 0


def test_oracle_reports_failure_when_greedy_misses_target() -> None:
    outcomes = {
        ("a",): _outcome(score_delta=1.0, recommendation="weak_match"),
        ("b",): _outcome(score_delta=1.0, recommendation="weak_match"),
        ("a", "b"): _outcome(
            score_delta=4.0, recommendation="strong_match"
        ),
    }
    optimal = exhaustive_optimal_action_set(
        _evaluator(outcomes), ["a", "b"]
    )
    assert optimal == ("a", "b")
    report = regret_report(
        greedy_ids=[],
        greedy_outcome=None,
        optimal_ids=optimal,
        optimal_outcome=outcomes[("a", "b")],
    )
    assert report["recommendation_success_regret"] == 1
    assert report["set_size_regret"] is None


def test_oracle_returns_none_when_no_subset_reaches_target() -> None:
    outcomes = {
        ("a",): _outcome(score_delta=1.0, recommendation="weak_match"),
        ("b",): _outcome(score_delta=1.0, recommendation="weak_match"),
        ("a", "b"): _outcome(score_delta=2.0, recommendation="weak_match"),
    }
    optimal = exhaustive_optimal_action_set(
        _evaluator(outcomes), ["a", "b"]
    )
    assert optimal is None


def test_regret_report_tracks_confidence_and_objective_optimality() -> None:
    greedy = _outcome(
        score_delta=5.0,
        recommendation="strong_match",
        confidence_delta=0.2,
    )
    optimal = _outcome(
        score_delta=5.0,
        recommendation="strong_match",
        confidence_delta=0.6,
    )
    report = regret_report(
        greedy_ids=["a"],
        greedy_outcome=greedy,
        optimal_ids=("b",),
        optimal_outcome=optimal,
    )
    assert report["set_size_regret"] == 0
    assert report["score_regret"] == 0
    assert report["confidence_regret"] == 0.4
    assert report["objective_optimal"] is False
    assert report["exact_optimal_set"] is False
