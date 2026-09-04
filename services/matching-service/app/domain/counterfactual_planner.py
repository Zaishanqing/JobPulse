"""Counterfactual planner v3: real scorer lookahead and minimal action sets.

The planner never trusts estimated deltas.  Every candidate action is applied
to a copy of the CV profile and the formal evaluator is re-run, so ranking and
the greedy minimal action set are backed by actual score/gate/recommendation
changes.  The evaluation callback receives a normalized action-set
``tuple[action_id, ...]`` (not a single action id); ranking evaluates singletons
and minimal-set search evaluates ``selected + [candidate]``, so non-linear
scorer interactions such as synergy, redundancy and conflict are respected.
It is a pure ranking/selection layer: the application/run script supplies the
evaluation callback.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

GATE_RANK = {
    "failed": 0,
    "uncertain": 1,
    "passed": 2,
    "not_applicable": 2,
}
RECOMMENDATION_RANK = {
    "not_recommended": 0,
    "weak_match": 1,
    "potential_match": 2,
    "strong_match": 3,
    "insufficient_information": 0,
}


@dataclass(frozen=True)
class LookaheadOutcome:
    action_id: str
    score_delta: float
    gate_rank_delta: int
    recommendation_rank_delta: int
    confidence_delta: float
    effort: float
    scenario_score: float | None = None
    scenario_hard_gate_status: str | None = None
    scenario_recommendation: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class PlannerStep:
    action_id: str
    marginal_score_delta: float
    marginal_gate_rank_delta: int
    marginal_recommendation_rank_delta: int
    marginal_confidence_delta: float
    cumulative_score_delta: float
    scenario_score: float | None
    scenario_hard_gate_status: str | None
    scenario_recommendation: str | None


@dataclass(frozen=True)
class MinimalActionSetResult:
    selected_action_ids: tuple[str, ...]
    steps: tuple[PlannerStep, ...]
    final_outcome: LookaheadOutcome | None
    reached_target: bool
    stop_reason: str


class CounterfactualPlannerV3:
    """Rank actions by formal lookahead, then build a greedy minimal set."""

    algorithm_version = "counterfactual-planner.v3"

    def __init__(
        self,
        evaluate_callback: Callable[[tuple[str, ...]], LookaheadOutcome],
        *,
        baseline_score: float | None,
        baseline_hard_gate_status: str | None,
        baseline_recommendation: str | None,
        max_actions: int = 10,
        marginal_delta_epsilon: float = 0.05,
    ) -> None:
        self._evaluate = evaluate_callback
        self._baseline_score = baseline_score
        self._baseline_gate = baseline_hard_gate_status
        self._baseline_recommendation = baseline_recommendation
        self._max_actions = max_actions
        self._epsilon = marginal_delta_epsilon

    def rank(self, action_ids: Sequence[str]) -> tuple[LookaheadOutcome, ...]:
        outcomes = [
            self._evaluate(tuple(sorted((action_id,))))
            for action_id in action_ids
        ]
        return tuple(
            sorted(
                outcomes,
                key=lambda item: (
                    -item.gate_rank_delta,
                    -item.recommendation_rank_delta,
                    -item.score_delta,
                    -item.confidence_delta,
                    item.effort,
                    item.action_id,
                ),
            )
        )

    def minimal_action_set(
        self,
        action_ids: Sequence[str],
        *,
        target_recommendation: str = "strong_match",
    ) -> MinimalActionSetResult:
        """Greedy marginal selection with real re-evaluation each round.

        Each round evaluates ``selected + [candidate]`` through the formal
        scorer (the callback receives the full normalized action set) and keeps
        the candidate with the best lexicographic marginal improvement.  The
        result carries the final set-level outcome (``evaluate(sorted(selected))``)
        instead of a singleton rerank, so callers can never mistake a single
        action outcome for the final set outcome.
        """

        target_rank = RECOMMENDATION_RANK.get(target_recommendation, 3)
        baseline_rank = RECOMMENDATION_RANK.get(
            self._baseline_recommendation or "", 0
        )
        if baseline_rank >= target_rank:
            return MinimalActionSetResult(
                selected_action_ids=(),
                steps=(),
                final_outcome=None,
                reached_target=True,
                stop_reason="baseline_already_at_target",
            )
        selected: list[str] = []
        steps: list[PlannerStep] = []
        cumulative_score_delta = 0.0
        cumulative_gate_rank_delta = 0
        cumulative_recommendation_rank_delta = 0
        cumulative_confidence_delta = 0.0
        remaining = list(action_ids)
        outcome_cache: dict[tuple[str, ...], LookaheadOutcome] = {}
        stop_reason = "target_reached"
        stopped_no_improvement = False
        while remaining and len(selected) < self._max_actions:
            best: tuple[PlannerStep, LookaheadOutcome, str] | None = None
            for candidate in remaining:
                combination = tuple(sorted([*selected, candidate]))
                if combination not in outcome_cache:
                    outcome_cache[combination] = self._evaluate(combination)
                outcome = outcome_cache[combination]
                marginal_score = outcome.score_delta - cumulative_score_delta
                marginal_gate = (
                    outcome.gate_rank_delta - cumulative_gate_rank_delta
                )
                marginal_recommendation = (
                    outcome.recommendation_rank_delta
                    - cumulative_recommendation_rank_delta
                )
                marginal_confidence = (
                    outcome.confidence_delta - cumulative_confidence_delta
                )
                step = PlannerStep(
                    action_id=candidate,
                    marginal_score_delta=round(marginal_score, 6),
                    marginal_gate_rank_delta=marginal_gate,
                    marginal_recommendation_rank_delta=marginal_recommendation,
                    marginal_confidence_delta=round(marginal_confidence, 6),
                    cumulative_score_delta=round(outcome.score_delta, 6),
                    scenario_score=outcome.scenario_score,
                    scenario_hard_gate_status=outcome.scenario_hard_gate_status,
                    scenario_recommendation=outcome.scenario_recommendation,
                )
                key = (
                    -step.marginal_gate_rank_delta,
                    -step.marginal_recommendation_rank_delta,
                    -step.marginal_score_delta,
                    -step.marginal_confidence_delta,
                    outcome.effort,
                    candidate,
                )
                if best is None or key < best[2]:
                    best = (step, outcome, key)
            assert best is not None
            step, _outcome, _key = best
            if (
                step.marginal_gate_rank_delta <= 0
                and step.marginal_recommendation_rank_delta <= 0
                and step.marginal_score_delta <= self._epsilon
            ):
                stop_reason = "no_improvement"
                stopped_no_improvement = True
                break
            selected.append(step.action_id)
            remaining.remove(step.action_id)
            steps.append(step)
            cumulative_score_delta = step.cumulative_score_delta
            cumulative_gate_rank_delta += step.marginal_gate_rank_delta
            cumulative_recommendation_rank_delta += (
                step.marginal_recommendation_rank_delta
            )
            cumulative_confidence_delta += step.marginal_confidence_delta
            if (
                step.scenario_recommendation
                and RECOMMENDATION_RANK.get(
                    step.scenario_recommendation, 0
                )
                >= target_rank
            ):
                stop_reason = "target_reached"
                break
        stopped_by_cap = bool(remaining) and len(selected) >= self._max_actions
        if not stopped_no_improvement and stopped_by_cap:
            stop_reason = "action_cap"
        final_combination = tuple(sorted(selected))
        final_outcome = (
            self._evaluate(final_combination) if selected else None
        )
        reached_target = bool(
            final_outcome
            and final_outcome.scenario_recommendation
            and RECOMMENDATION_RANK.get(
                final_outcome.scenario_recommendation, 0
            )
            >= target_rank
        )
        if (
            stop_reason == "target_reached"
            and not reached_target
            and not stopped_by_cap
        ):
            stop_reason = "no_improvement"
        return MinimalActionSetResult(
            selected_action_ids=tuple(selected),
            steps=tuple(steps),
            final_outcome=final_outcome,
            reached_target=reached_target,
            stop_reason=stop_reason,
        )


__all__ = [
    "GATE_RANK",
    "CounterfactualPlannerV3",
    "LookaheadOutcome",
    "MinimalActionSetResult",
    "PlannerStep",
    "RECOMMENDATION_RANK",
]
