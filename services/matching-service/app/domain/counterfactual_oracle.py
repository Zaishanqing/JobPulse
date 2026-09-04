"""Exhaustive minimal-action-set oracle for counterfactual planner v3.

For a small candidate set the formal scorer can evaluate every non-empty
subset, giving the true optimal Minimal Action Set.  Greedy selection is then
compared against this deterministic oracle with set-size/score/recommendation
regrets and an exact optimal-set rate.  No Human Gold is needed because the
formal scorer itself defines the oracle.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from itertools import combinations

from app.domain.counterfactual_planner import (
    RECOMMENDATION_RANK,
    LookaheadOutcome,
)


Evaluate = Callable[[tuple[str, ...]], LookaheadOutcome]


def exhaustive_optimal_action_set(
    evaluate: Evaluate,
    action_ids: Sequence[str],
    *,
    target_recommendation: str = "strong_match",
    baseline_recommendation: str | None = None,
) -> tuple[str, ...] | None:
    """Return the smallest successful subset, tie-broken by score/confidence.

    Returns ``None`` when no non-empty subset reaches the recommendation
    target.  Ties keep the higher cumulative score delta, then higher
    confidence delta, then the lexicographically smaller action set.
    """

    target_rank = RECOMMENDATION_RANK.get(target_recommendation, 3)
    if (
        baseline_recommendation
        and RECOMMENDATION_RANK.get(baseline_recommendation, 0)
        >= target_rank
    ):
        return ()
    candidates: list[tuple[tuple[str, ...], LookaheadOutcome]] = []
    ids = tuple(sorted(action_ids))
    for size in range(1, len(ids) + 1):
        for subset in combinations(ids, size):
            outcome = evaluate(tuple(sorted(subset)))
            if (
                outcome.scenario_recommendation
                and RECOMMENDATION_RANK.get(
                    outcome.scenario_recommendation, 0
                )
                >= target_rank
            ):
                candidates.append((subset, outcome))
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: (
            len(item[0]),
            -item[1].score_delta,
            -item[1].confidence_delta,
            item[0],
        ),
    )[0]


def regret_report(
    *,
    greedy_ids: Sequence[str],
    greedy_outcome: LookaheadOutcome | None,
    optimal_ids: Sequence[str] | None,
    optimal_outcome: LookaheadOutcome | None,
    target_recommendation: str = "strong_match",
    greedy_reached_target: bool | None = None,
    baseline_reached_target: bool = False,
) -> dict:
    """Quantify greedy regret against the exhaustive oracle."""

    target_rank = RECOMMENDATION_RANK.get(target_recommendation, 3)
    greedy_success = (
        greedy_reached_target
        if greedy_reached_target is not None
        else bool(
            greedy_outcome
            and greedy_outcome.scenario_recommendation
            and RECOMMENDATION_RANK.get(
                greedy_outcome.scenario_recommendation, 0
            )
            >= target_rank
        )
    )
    optimal_success = baseline_reached_target or bool(
        optimal_ids
        and optimal_outcome
        and optimal_outcome.scenario_recommendation
        and RECOMMENDATION_RANK.get(
            optimal_outcome.scenario_recommendation, 0
        )
        >= target_rank
    )
    if greedy_success and optimal_success:
        size_regret = len(greedy_ids) - len(optimal_ids)
        score_regret = max(
            float(optimal_outcome.score_delta if optimal_outcome else 0.0)
            - float(greedy_outcome.score_delta if greedy_outcome else 0.0),
            0.0,
        )
        confidence_regret = max(
            float(
                optimal_outcome.confidence_delta
                if optimal_outcome
                else 0.0
            )
            - float(
                greedy_outcome.confidence_delta if greedy_outcome else 0.0
            ),
            0.0,
        )
    else:
        size_regret = None
        score_regret = None
        confidence_regret = None
    return {
        "greedy_ids": tuple(sorted(greedy_ids)),
        "greedy_success": greedy_success,
        "optimal_ids": tuple(sorted(optimal_ids)) if optimal_ids else None,
        "optimal_success": optimal_success,
        "set_size_regret": size_regret,
        "score_regret": round(score_regret, 6) if score_regret is not None else None,
        "recommendation_success_regret": int(
            (not greedy_success) and optimal_success
        ),
        "confidence_regret": (
            round(confidence_regret, 6)
            if confidence_regret is not None
            else None
        ),
        "objective_optimal": bool(
            greedy_success
            and optimal_success
            and size_regret == 0
            and score_regret == 0
            and confidence_regret == 0
        ),
        "exact_optimal_set": (
            tuple(sorted(greedy_ids)) == tuple(sorted(optimal_ids))
            if greedy_success and optimal_success
            else False
        ),
    }


__all__ = [
    "Evaluate",
    "exhaustive_optimal_action_set",
    "regret_report",
]
