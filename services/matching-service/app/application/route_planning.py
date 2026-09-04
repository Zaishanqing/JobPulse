"""Deterministic learning-route planning with bounded formal rescoring."""

from __future__ import annotations

from collections.abc import Callable
from itertools import combinations
from typing import Literal

from app.application.learning_catalog import responsibility_action_template
from app.application.what_if import WhatIfService
from app.domain.evaluation import MatchEvaluation
from app.domain.gaps import (
    ActionCost,
    GapAnalysis,
    LearningRoute,
    MinimalActionSet,
    PrioritizedGap,
    SkillPathDecision,
)
from app.domain.profiles import CVMatchProfile, PositionMatchProfile
from app.domain.what_if import CostBand, WhatIfAction, WhatIfResult

_LEVELS = ("unknown", "basic", "working", "proficient", "advanced", "expert")
_OWNERSHIP = {
    "unknown": 0,
    "declared": 0,
    "used": 1,
    "participated": 1,
    "implemented": 2,
    "owned": 3,
    "designed": 4,
    "led": 5,
}
_HARD_ACTION_HOURS = {
    "availability": 2.0,
    "location": 8.0,
    "certificate": 40.0,
    "language": 80.0,
    "education": 160.0,
    "experience": 240.0,
}
_ACTION_TYPE_BUCKET = {
    "add_skill": "direct_skill",
    "add_project_experience": "evidence_project",
    "strengthen_evidence": "evidence_project",
    "strengthen_ownership": "ownership",
    "controlled_skill_transfer": "controlled_transfer",
    "satisfy_hard_condition": "hard_gate",
}
_BUCKET_CAPS = {
    "direct_skill": 3,
    "evidence_project": 2,
    "ownership": 2,
    # 3 candidate slots so the final trim can keep the two highest-value
    # transfer targets instead of being forced to the first two gaps.
    "controlled_transfer": 3,
    "hard_gate": 1,
}


class LearningRoutePlanner:
    """Build three deterministic route views from one bounded candidate space."""

    def __init__(
        self,
        what_if: WhatIfService,
        *,
        max_actions: int = 3,
        max_candidate_actions: int = 8,
        exhaustive_limit: int = 8,
        beam_width: int = 32,
    ) -> None:
        if max_actions < 1:
            raise ValueError("max_actions must be positive")
        if max_candidate_actions < max_actions:
            raise ValueError("max_candidate_actions must be >= max_actions")
        self._what_if = what_if
        self._max_selected_actions = max_actions
        self._max_candidate_actions = max_candidate_actions
        self._exhaustive_limit = exhaustive_limit
        self._beam_width = beam_width

    def plan(
        self,
        cv: CVMatchProfile,
        position: PositionMatchProfile,
        evaluation: MatchEvaluation,
        gap_analysis: GapAnalysis,
        *,
        time_budget_hours: float | None,
        target_type: str,
        use_enterprise_weights: bool,
        skill_path_decisions: tuple[SkillPathDecision, ...] = (),
    ) -> tuple[tuple[WhatIfAction, ...], tuple[LearningRoute, ...], MinimalActionSet]:
        planning_gaps = self._select_planning_gaps(gap_analysis.prioritized_gaps)
        actions = self._select_actions(
            self._action_groups(
                planning_gaps,
                position,
                skill_path_decisions,
                evaluation=evaluation,
            ),
            self._max_candidate_actions,
        )
        request = {
            "baseline_evaluation": evaluation.model_dump(mode="python"),
            "cv_profile": cv.model_dump(mode="python"),
            "position_profile": position.model_dump(mode="python"),
            "target_type": target_type,
            "use_enterprise_weights": use_enterprise_weights,
        }
        # What-if scoring is deterministic for one immutable evaluation.  The
        # three route views overlap heavily, so reuse identical scenario scores
        # instead of asking the evaluator to recompute them.
        scenario_cache: dict[tuple[str, ...], WhatIfResult] = {}

        def evaluate(selected: tuple[WhatIfAction, ...]) -> WhatIfResult:
            key = tuple(sorted(item.action_id for item in selected))
            if key not in scenario_cache:
                scenario_cache[key] = self._evaluate(request, selected)
            return scenario_cache[key]

        if not actions:
            return (
                (),
                (),
                self._minimal_action_set(
                    (),
                    gap_analysis.prioritized_gaps,
                    cv,
                    position,
                    evaluation,
                    request,
                    time_budget_hours,
                    evaluate=evaluate,
                ),
            )
        all_actions = self._annotate_actions(actions, evaluation, evaluate)
        actions = self._positive_action_closure(all_actions)
        actions = self._prefer_high_value_bonus_actions(
            actions, gap_analysis.prioritized_gaps
        )
        actions = self._prefer_high_value_transfers(
            actions, gap_analysis.prioritized_gaps
        )
        minimal = self._minimal_action_set(
            actions,
            gap_analysis.prioritized_gaps,
            cv,
            position,
            evaluation,
            request,
            time_budget_hours,
            evaluate=evaluate,
        )
        outcomes = self._outcomes(
            actions, gap_analysis.prioritized_gaps, cv, evaluate
        )
        if not outcomes:
            return actions, (), minimal

        selected_sets: set[tuple[str, ...]] = set()
        routes: list[LearningRoute] = []
        selectors = (
            ("fastest_employment", self._fastest_key),
            ("budget_max_gain", lambda item: self._budget_key(item, time_budget_hours)),
            ("foundation_first", self._foundation_key),
        )
        for route_type, key in selectors:
            available = [
                item
                for item in outcomes
                if tuple(action.action_id for action in item[0]) not in selected_sets
                and (
                    route_type != "budget_max_gain"
                    or time_budget_hours is None
                    or self._cost(item[0]) <= time_budget_hours
                )
            ]
            if not available:
                continue
            chosen = min(available, key=key)
            action_ids = tuple(item.action_id for item in chosen[0])
            selected_sets.add(action_ids)
            result = chosen[1]
            radar_evaluation = result.projected_evaluation or result.scenario_evaluation
            routes.append(
                LearningRoute(
                    route_type=route_type,
                    action_ids=action_ids,
                    total_cost_hours=self._cost(chosen[0]),
                    baseline_score=result.baseline_score,
                    modeled_final_score=(
                        result.projected_score
                        if result.projected_score is not None
                        else result.scenario_score
                    ),
                    modeled_score_delta=(
                        result.projected_score_delta
                        if result.projected_score_delta is not None
                        else result.score_delta
                    ),
                    modeled_confidence_delta=result.confidence_delta,
                    final_score=(
                        result.projected_score
                        if result.projected_score is not None
                        else result.scenario_score
                    ),
                    projected_match_gain=(
                        result.projected_score_delta
                        if result.projected_score_delta is not None
                        else result.score_delta
                    ),
                    confidence_gain=result.confidence_delta,
                    target_reachable=self._reachable(result),
                    final_recommendation=(
                        result.projected_recommendation
                        if result.projected_recommendation is not None
                        else result.scenario_recommendation
                    ),
                    remaining_blocker_ids=self._remaining_blockers(
                        gap_analysis.prioritized_gaps, chosen[0], result
                    ),
                    path_refs=tuple(
                        sorted({ref for action in chosen[0] for ref in action.path_refs})
                    ),
                    action_costs=self._action_costs(
                        chosen[0],
                        frozenset(action.action_id for action in chosen[0]),
                    ),
                    scenario_dimension_scores=(
                        radar_evaluation.final_match_result.dimension_scores
                        if radar_evaluation is not None
                        and radar_evaluation.final_match_result is not None
                        else ()
                    ),
                )
            )
        return (
            actions,
            tuple(routes),
            minimal,
        )

    @staticmethod
    def _positive_action_closure(
        all_actions: tuple[WhatIfAction, ...],
    ) -> tuple[WhatIfAction, ...]:
        """Keep positive standalone actions plus their prerequisite closure."""
        by_id = {item.action_id: item for item in all_actions}
        positive_ids = {
            item.action_id
            for item in all_actions
            if (item.estimated_score_delta or 0.0) > 0
        }
        required_ids: set[str] = set()

        def add_required(action_id: str) -> None:
            if action_id in required_ids:
                return
            required_ids.add(action_id)
            action = by_id.get(action_id)
            if action is None:
                return
            for required_id in action.requires_action_ids:
                add_required(required_id)

        for action_id in positive_ids:
            add_required(action_id)
        return tuple(
            action
            for action in all_actions
            if action.action_id in positive_ids or action.action_id in required_ids
        )

    def candidate_actions(
        self,
        position: PositionMatchProfile,
        gap_analysis: GapAnalysis,
        *,
        skill_path_decisions: tuple[SkillPathDecision, ...] = (),
        evaluation: MatchEvaluation | None = None,
    ) -> tuple[WhatIfAction, ...]:
        """Build the bounded action set without running scenario search.

        Evidence-deletion diagnostics compare whether deleting evidence changes
        the generated action set.  Solving fastest/budget/minimal routes there
        would enumerate and rescore many action combinations twice, although
        none of those route outcomes is used by the faithfulness decision.
        """
        planning_gaps = self._select_planning_gaps(gap_analysis.prioritized_gaps)
        return self._select_actions(
            self._action_groups(
                planning_gaps,
                position,
                skill_path_decisions,
                evaluation=evaluation,
            ),
            self._max_candidate_actions,
        )

    def _evaluate(
        self,
        request: dict[str, object],
        actions: tuple[WhatIfAction, ...],
    ) -> WhatIfResult:
        return self._what_if.evaluate(
            {
                **request,
                "actions": [item.model_dump(mode="python") for item in actions],
            }
        )

    def _annotate_actions(
        self,
        actions: tuple[WhatIfAction, ...],
        evaluation: MatchEvaluation,
        evaluate: Callable[[tuple[WhatIfAction, ...]], WhatIfResult],
    ) -> tuple[WhatIfAction, ...]:
        by_id = {item.action_id: item for item in actions}
        output = []
        for action in actions:
            closure = self._dependency_closure(action, by_id)
            result = evaluate(closure)
            delta = (
                result.projected_score_delta
                if result.generation_status == "completed"
                and result.projected_score_delta is not None
                else result.score_delta
                if result.generation_status == "completed"
                else None
            )
            reason = self._score_effect_reason(action, evaluation, delta)
            utility = round(delta / max(self._cost(closure), 1.0), 6) if delta is not None else None
            output.append(
                action.model_copy(
                    update={
                        "estimated_score_delta": delta,
                        "estimated_utility": utility,
                        "score_effect_reason": reason,
                    }
                )
            )
        return tuple(output)

    def _minimal_action_set(
        self,
        actions: tuple[WhatIfAction, ...],
        gaps: tuple[PrioritizedGap, ...],
        cv: CVMatchProfile,
        position: PositionMatchProfile,
        evaluation: MatchEvaluation,
        request: dict[str, object],
        time_budget_hours: float | None,
        *,
        evaluate: Callable[[tuple[WhatIfAction, ...]], WhatIfResult] | None = None,
    ) -> MinimalActionSet:
        """Find a minimum-cardinality set inside a bounded candidate search.

        Small candidate spaces stay exhaustive. Larger spaces keep only the
        best estimated combinations at every cardinality, preventing exponential
        What-if rescoring while preserving deterministic ordering.
        """
        evaluate = evaluate or (lambda selected: self._evaluate(request, selected))
        bounded_beam = len(actions) > self._exhaustive_limit
        final = evaluation.final_match_result
        if final is None:
            raise ValueError("minimal action search requires a final match result")
        if self._evaluation_reachable(evaluation) and not gaps:
            return self._minimal_result(
                status="already_satisfied",
                actions=actions,
                selected=(),
                gaps=gaps,
                evaluation=evaluation,
                position=position,
                result=None,
                time_budget_hours=time_budget_hours,
                unreachable_reason_codes=(),
                search_status="bounded_beam" if bounded_beam else "exact_bounded",
            )
        immutable_failures = tuple(
            item
            for item in evaluation.hard_constraint_results
            if item.status == "fail" and item.constraint_type in {"education", "experience"}
        )
        if immutable_failures:
            return self._minimal_result(
                status="hard_blocked",
                actions=actions,
                selected=(),
                gaps=gaps,
                evaluation=evaluation,
                position=position,
                result=None,
                time_budget_hours=time_budget_hours,
                unreachable_reason_codes=tuple(
                    f"IMMUTABLE_HARD_CONDITION:{item.constraint_type}:{item.requirement_id}"
                    for item in immutable_failures
                ),
                search_status="bounded_beam" if bounded_beam else "exact_bounded",
            )

        if not actions:
            reasons = []
            if any(not gap.position_evidence_present for gap in gaps):
                reasons.append("POSITION_EVIDENCE_INSUFFICIENT")
            reasons.append("NO_POSITIVE_SCORE_ACTION")
            return self._minimal_result(
                status=(
                    "position_evidence_insufficient"
                    if "POSITION_EVIDENCE_INSUFFICIENT" in reasons
                    else "no_positive_actions"
                ),
                actions=(),
                selected=(),
                gaps=gaps,
                evaluation=evaluation,
                position=position,
                result=None,
                time_budget_hours=time_budget_hours,
                unreachable_reason_codes=tuple(reasons),
                search_status="bounded_beam" if bounded_beam else "exact_bounded",
            )

        attempted: list[WhatIfResult] = []
        budget_excluded = False
        chosen: tuple[tuple[WhatIfAction, ...], WhatIfResult] | None = None
        for size in range(1, min(len(actions), self._max_selected_actions) + 1):
            reachable: list[tuple[tuple[WhatIfAction, ...], WhatIfResult]] = []
            candidates = [
                selected
                for selected in combinations(actions, size)
                if self._prerequisites_closed(selected, gaps, cv)
            ]
            if bounded_beam:
                candidates = self._bounded_candidates(candidates, actions)
            for selected in candidates:
                if time_budget_hours is not None and self._cost(selected) > time_budget_hours:
                    budget_excluded = True
                    continue
                result = evaluate(selected)
                if result.generation_status != "completed":
                    continue
                attempted.append(result)
                if self._reachable(result):
                    reachable.append((selected, result))
            if reachable:
                chosen = min(
                    reachable,
                    key=lambda item: (
                        self._cost(item[0]),
                        -(item[1].scenario_score or 0.0),
                        tuple(action.action_id for action in item[0]),
                    ),
                )
                break

        if chosen is not None:
            return self._minimal_result(
                status="reached",
                actions=actions,
                selected=chosen[0],
                gaps=gaps,
                evaluation=evaluation,
                position=position,
                result=chosen[1],
                time_budget_hours=time_budget_hours,
                unreachable_reason_codes=(),
                search_status="bounded_beam" if bounded_beam else "exact_bounded",
            )
        reasons = []
        if budget_excluded:
            reasons.append("BUDGET_EXCLUDES_CANDIDATES")
        if not attempted:
            reasons.append("NO_VALID_ACTION_COMBINATION")
            if budget_excluded:
                return self._minimal_result(
                    status="budget_excluded",
                    actions=actions,
                    selected=(),
                    gaps=gaps,
                    evaluation=evaluation,
                    position=position,
                    result=None,
                    time_budget_hours=time_budget_hours,
                    unreachable_reason_codes=tuple(reasons),
                    search_status=(
                        "bounded_beam" if bounded_beam else "exact_bounded"
                    ),
                )
        elif final.hard_gate_status == "failed" and all(
            item.scenario_hard_gate_status == "failed" for item in attempted
        ):
            reasons.append("HARD_GATE_UNRESOLVED")
        else:
            reasons.append("TARGET_STATE_UNREACHED")
        return self._minimal_result(
            status="unreachable",
            actions=actions,
            selected=(),
            gaps=gaps,
            evaluation=evaluation,
            position=position,
            result=None,
            time_budget_hours=time_budget_hours,
            unreachable_reason_codes=tuple(reasons),
            search_status="bounded_beam" if bounded_beam else "exact_bounded",
        )

    def _minimal_result(
        self,
        *,
        status: Literal[
            "reached",
            "already_satisfied",
            "hard_blocked",
            "position_evidence_insufficient",
            "no_positive_actions",
            "budget_excluded",
            "unreachable",
        ],
        actions: tuple[WhatIfAction, ...],
        selected: tuple[WhatIfAction, ...],
        gaps: tuple[PrioritizedGap, ...],
        evaluation: MatchEvaluation,
        position: PositionMatchProfile,
        result: WhatIfResult | None,
        time_budget_hours: float | None,
        unreachable_reason_codes: tuple[str, ...],
        search_status: Literal["exact_bounded", "bounded_beam"],
    ) -> MinimalActionSet:
        final = evaluation.final_match_result
        if final is None:
            raise ValueError("minimal action result requires a final match result")
        selected_ids = frozenset(item.action_id for item in selected)
        total_cost = self._cost(selected)
        covered = tuple(
            sorted(
                {
                    requirement_id
                    for action in selected
                    for requirement_id in action.target_requirement_ids
                }
            )
        )
        evidence_by_key = {
            evidence.model_dump_json(): evidence
            for gap in gaps
            if gap.requirement_id in covered
            for evidence in gap.evidence
        }
        scenario_hard_gate = result.scenario_hard_gate_status if result is not None else None
        projected = result is not None and result.projected_if_completed
        return MinimalActionSet(
            status=status,
            source_evaluation_id=evaluation.evaluation_id,
            scenario_id=result.scenario_id if result is not None else None,
            selected_action_ids=tuple(item.action_id for item in selected),
            deferred_action_ids=tuple(
                item.action_id for item in actions if item.action_id not in selected_ids
            ),
            action_costs=self._action_costs(actions, selected_ids),
            minimum_action_count=len(selected),
            total_cost_hours=total_cost,
            budget_hours=time_budget_hours,
            budget_used_hours=total_cost,
            budget_remaining_hours=(
                round(max(0.0, time_budget_hours - total_cost), 4)
                if time_budget_hours is not None
                else None
            ),
            baseline_score=final.overall_score,
            modeled_final_score=(
                (result.projected_score or result.scenario_score)
                if result is not None
                else None
            ),
            modeled_score_delta=(
                (
                    result.projected_score_delta
                    if result.projected_score_delta is not None
                    else result.score_delta
                )
                if result is not None
                else None
            ),
            modeled_confidence_delta=(
                result.confidence_delta if result is not None else None
            ),
            scenario_score=(
                (result.projected_score or result.scenario_score)
                if result is not None
                else None
            ),
            score_delta=(
                (
                    result.projected_score_delta
                    if result.projected_score_delta is not None
                    else result.score_delta
                )
                if result is not None
                else None
            ),
            dimension_deltas=result.dimension_deltas if result is not None else (),
            baseline_hard_gate_status=final.hard_gate_status,
            scenario_hard_gate_status=scenario_hard_gate,
            hard_gate_delta=(
                f"{final.hard_gate_status}->{scenario_hard_gate}"
                if scenario_hard_gate is not None
                else None
            ),
            target_reachable=status in {"reached", "already_satisfied"},
            covered_requirement_ids=covered,
            evidence_refs=tuple(evidence_by_key[key] for key in sorted(evidence_by_key)),
            path_refs=tuple(sorted({ref for action in selected for ref in action.path_refs})),
            unreachable_reason_codes=unreachable_reason_codes,
            cv_profile_version=evaluation.cv_profile_version,
            position_profile_version=evaluation.position_profile_version,
            graph_version_id=position.graph_version,
            policy_version=(
                f"{evaluation.algorithm_version}|{final.algorithm_version}|"
                f"{final.scoring_config_version}"
            ),
            search_status=search_status,
            algorithm_version="minimal-action-set.v3",
            projected_if_completed=projected,
        )

    def _action_costs(
        self,
        actions: tuple[WhatIfAction, ...],
        selected_ids: frozenset[str],
    ) -> tuple[ActionCost, ...]:
        by_id = {item.action_id: item for item in actions}
        output = []
        for action in actions:
            closure = self._dependency_closure(action, by_id)
            dependency_hours = max(0.0, self._cost(closure) - action.estimated_hours)
            band = action.cost_band
            expected = band.expected_hours if band is not None else action.estimated_hours
            min_hours = band.min_hours if band is not None else expected
            max_hours = band.max_hours if band is not None else expected
            cost_confidence = band.confidence if band is not None else 0.0
            difficulty = (
                "low"
                if expected <= 8
                else "medium"
                if expected <= 24
                else "high"
            )
            output.append(
                ActionCost(
                    action_id=action.action_id,
                    direct_hours=expected,
                    dependency_hours=round(dependency_hours, 4),
                    total_hours=self._cost(closure),
                    min_hours=round(min_hours, 4),
                    max_hours=round(max_hours, 4),
                    cost_confidence=round(cost_confidence, 6),
                    difficulty=difficulty,
                    selected=action.action_id in selected_ids,
                    cost_model=action.cost_model,
                    cost_source_type="heuristic",
                    cost_source_ref=action.cost_model,
                    estimate_status="estimated",
                )
            )
        return tuple(output)

    @staticmethod
    def _evaluation_reachable(evaluation: MatchEvaluation) -> bool:
        final = evaluation.final_match_result
        return bool(
            final is not None
            and final.hard_gate_status in {"passed", "not_applicable"}
            and final.recommendation_level in {"potential_match", "strong_match"}
        )

    @staticmethod
    def _dependency_closure(
        action: WhatIfAction, by_id: dict[str, WhatIfAction]
    ) -> tuple[WhatIfAction, ...]:
        selected: dict[str, WhatIfAction] = {}

        def add(item: WhatIfAction) -> None:
            for required_id in item.requires_action_ids:
                required = by_id.get(required_id)
                if required is not None and required.action_id not in selected:
                    add(required)
            selected[item.action_id] = item

        add(action)
        order = {item_id: index for index, item_id in enumerate(by_id)}
        return tuple(sorted(selected.values(), key=lambda item: order[item.action_id]))

    @staticmethod
    def _score_effect_reason(
        action: WhatIfAction,
        evaluation: MatchEvaluation,
        delta: float | None,
    ) -> str | None:
        if delta is None:
            return "SCENARIO_EVALUATION_REJECTED"
        if abs(delta) > 1e-9:
            return None
        if action.action_type == "satisfy_hard_condition":
            return "HARD_CONDITION_UNCHANGED"
        if action.ownership and action.skill_id:
            baseline = next(
                (item for item in evaluation.skill_results if item.skill_id == action.skill_id),
                None,
            )
            if baseline is None or baseline.match_status == "missing":
                return "SKILL_MISSING_BEFORE_OWNERSHIP"
        if action.requires_action_ids:
            return "COMPOSITE_OR_PREREQUISITE_INTERACTION"
        return "NO_FORMAL_SCORE_CHANGE"

    def _outcomes(
        self,
        actions: tuple[WhatIfAction, ...],
        gaps: tuple[PrioritizedGap, ...],
        cv: CVMatchProfile,
        evaluate: Callable[[tuple[WhatIfAction, ...]], WhatIfResult],
    ) -> list[tuple[tuple[WhatIfAction, ...], WhatIfResult]]:
        outcomes: list[tuple[tuple[WhatIfAction, ...], WhatIfResult]] = []
        for size in range(1, min(len(actions), self._max_selected_actions) + 1):
            candidates = [
                selected
                for selected in combinations(actions, size)
                if self._prerequisites_closed(selected, gaps, cv)
            ]
            if len(actions) > self._exhaustive_limit:
                # Keep the hot path bounded. Zero standalone gain remains eligible
                # because AND/min_count and prerequisites can create joint gain.
                candidates = self._bounded_candidates(candidates, actions)
            for selected in candidates:
                result = evaluate(selected)
                projected_delta = (
                    result.projected_score_delta
                    if result.projected_score_delta is not None
                    else result.score_delta
                )
                if result.generation_status == "completed" and (
                    self._reachable(result) or (projected_delta or 0.0) > 0
                ):
                    outcomes.append((selected, result))
        return outcomes

    @staticmethod
    def _beam_estimate(actions: tuple[WhatIfAction, ...]) -> tuple:
        estimated_gain = sum(item.estimated_score_delta or 0.0 for item in actions)
        cost = sum(item.estimated_hours for item in actions)
        return (-estimated_gain, cost, tuple(item.action_id for item in actions))

    def _bounded_candidates(
        self,
        candidates: list[tuple[WhatIfAction, ...]],
        actions: tuple[WhatIfAction, ...],
    ) -> list[tuple[WhatIfAction, ...]]:
        """Preserve the legacy priority core, then add beam-ranked expansions."""
        core_ids = {
            item.action_id for item in actions[: self._max_selected_actions]
        }
        core = [
            selected
            for selected in candidates
            if all(item.action_id in core_ids for item in selected)
        ]
        ranked = sorted(candidates, key=self._beam_estimate)[: self._beam_width]
        unique: dict[tuple[str, ...], tuple[WhatIfAction, ...]] = {}
        for selected in (*core, *ranked):
            unique.setdefault(
                tuple(item.action_id for item in selected),
                selected,
            )
        return list(unique.values())

    @staticmethod
    def _heuristic_hours(
        gap: PrioritizedGap,
        *,
        base: float,
        target_level: str | None = None,
        ownership: str | None = None,
        current_level: str | None = None,
    ) -> float:
        current = current_level or gap.current_level or "unknown"
        target = target_level or gap.target_level or current
        distance = 1
        if current in _LEVELS and target in _LEVELS:
            distance = max(1, _LEVELS.index(target) - _LEVELS.index(current))
        level_factor = 1.0 + 0.35 * (distance - 1)
        ownership_factor = 1.0 + 0.15 * max(0, _OWNERSHIP.get(ownership or "unknown", 0) - 2)
        transfer_discount = 1.0 - 0.25 * gap.transferability_score
        return round(max(1.0, base * level_factor * ownership_factor * transfer_discount), 2)

    @staticmethod
    def _cost_band(
        gap: PrioritizedGap,
        *,
        base: float,
        low_factor: float = 0.5,
        high_factor: float = 2.0,
        confidence: float = 0.25,
        target_level: str | None = None,
        ownership: str | None = None,
        current_level: str | None = None,
        basis: str,
    ) -> CostBand:
        """Wide, honest cost band instead of a false-precise fixed hour count."""
        current = current_level or gap.current_level or "unknown"
        target = target_level or gap.target_level or current
        distance = 1
        if current in _LEVELS and target in _LEVELS:
            distance = max(1, _LEVELS.index(target) - _LEVELS.index(current))
        level_factor = 1.0 + 0.35 * (distance - 1)
        ownership_factor = 1.0 + 0.15 * max(
            0, _OWNERSHIP.get(ownership or "unknown", 0) - 2
        )
        transfer_discount = 1.0 - 0.25 * gap.transferability_score
        expected = max(
            1.0,
            base * level_factor * ownership_factor * transfer_discount,
        )
        return CostBand(
            min_hours=round(max(0.5, expected * low_factor), 1),
            expected_hours=round(expected, 1),
            max_hours=round(expected * high_factor, 1),
            confidence=confidence,
            basis=basis,
        )

    @classmethod
    def _root_groups_for_requirement(
        cls, position: PositionMatchProfile, requirement_id: str
    ) -> tuple[str, ...]:
        graph = position.requirement_graph
        if graph is None:
            return ()
        groups = {item.requirement_group_id: item for item in graph.groups}
        referenced = {
            child.ref_id
            for group in graph.groups
            for child in group.children
            if child.node_type == "group_ref"
        }

        def contains(group_id: str) -> bool:
            return any(
                child.ref_id == requirement_id
                if child.node_type != "group_ref"
                else contains(child.ref_id)
                for child in groups[group_id].children
            )

        return tuple(
            group_id for group_id in groups if group_id not in referenced and contains(group_id)
        )

    @classmethod
    def _action_groups(
        cls,
        gaps: tuple[PrioritizedGap, ...],
        position: PositionMatchProfile,
        skill_path_decisions: tuple[SkillPathDecision, ...] = (),
        *,
        evaluation: MatchEvaluation | None = None,
    ) -> tuple[tuple[WhatIfAction, ...], ...]:
        groups: list[tuple[WhatIfAction, ...]] = []
        responsibility_by_id = {
            item.requirement_id: item
            for item in position.responsibility_requirements
        }
        paths_by_requirement = {
            item.target_requirement_id: item
            for item in skill_path_decisions
            if item.status == "reachable" and item.paths
        }
        satisfied_alternatives = {
            result_id
            for item in (evaluation.requirement_group_results if evaluation else ())
            if item.group_id.startswith("standard-clause:")
            and item.group_type in {"or", "one_of"}
            and item.status == "satisfied"
            for result_id in item.covered_result_ids
        }
        skill_results_by_id = {
            item.skill_id: item
            for item in (evaluation.skill_results if evaluation else ())
            if item.skill_id
        }
        for gap in gaps:
            if not gap.position_evidence_present:
                # The requirement cannot be grounded on the position side;
                # it must be reported as evidence insufficiency, not turned
                # into a candidate learning or evidence action.
                continue
            if (
                gap.requirement_id in satisfied_alternatives
                and gap.gap_type
                in {
                    "required_skill_missing",
                    "skill_level_gap",
                    "evidence_gap",
                    "usage_evidence_gap",
                }
            ):
                # A satisfied one-of clause means another atomic alternative
                # already fulfills this source requirement. Do not turn the
                # unused language/framework alternative into a learning task.
                continue
            output: list[WhatIfAction] = []
            base = gap.requirement_id.replace(":", "-")
            targets = (
                gap.requirement_id,
                *cls._root_groups_for_requirement(position, gap.requirement_id),
            )
            path_decision = paths_by_requirement.get(gap.requirement_id)
            responsibility = responsibility_by_id.get(gap.requirement_id)
            if path_decision is not None:
                chosen_path = path_decision.paths[0]
                transfer_band = CostBand(
                    min_hours=round(chosen_path.total_cost_hours * 0.5, 1),
                    expected_hours=round(chosen_path.total_cost_hours, 1),
                    max_hours=round(chosen_path.total_cost_hours * 1.5, 1),
                    confidence=0.4,
                    basis=f"transfer-path:{chosen_path.path_id}",
                )
                output.append(
                    WhatIfAction(
                        action_id=f"transfer-{base}",
                        action_type="controlled_skill_transfer",
                        skill_id=gap.skill_id,
                        source_skill_id=chosen_path.source_skill_id,
                        target_level=gap.target_level or "working",
                        target_requirement_ids=targets,
                        path_refs=(chosen_path.path_id,),
                        graph_version=position.graph_version,
                        estimated_hours=transfer_band.expected_hours,
                        cost_band=transfer_band,
                        stage="transfer",
                        cost_model="cost-band.v1",
                        milestone_status="planned",
                        score_credit_allowed=chosen_path.score_credit_allowed,
                        suitable_for_learning=True,
                        deliverable=f"通过 {chosen_path.source_skill_id} 迁移掌握 {gap.skill_id}",
                        acceptance_criteria=(
                            "迁移能力在真实任务中可复现",
                            "能力评估达到目标等级",
                        ),
                    )
                )
            if gap.gap_type == "hard_constraint_gap":
                condition = next(
                    (
                        item
                        for item in position.hard_conditions
                        if item.condition_id == gap.requirement_id
                    ),
                    None,
                )
                if (
                    condition is not None
                    and condition.resolution_status == "resolved"
                    and condition.condition_type not in {"education", "experience"}
                ):
                    expected = _HARD_ACTION_HOURS[condition.condition_type]
                    hard_band = CostBand(
                        min_hours=round(expected * 0.5, 1),
                        expected_hours=round(expected, 1),
                        max_hours=round(expected * 2.0, 1),
                        confidence=0.3,
                        basis=f"hard-condition:{condition.condition_type}",
                    )
                    output.append(
                        WhatIfAction(
                            action_id=f"hard-gate-{base}",
                            action_type="satisfy_hard_condition",
                            target_requirement_ids=targets,
                            estimated_hours=hard_band.expected_hours,
                            cost_band=hard_band,
                            stage="hard_gate",
                            cost_model="cost-band.v1",
                        )
                    )
            elif gap.skill_id and gap.gap_type in {
                "required_skill_missing",
                "bonus_skill_missing",
                "skill_level_gap",
            }:
                skill_name = gap.skill_id
                basic_id = f"learn-basic-{base}"
                learn_id = f"learn-{base}"
                project_id = f"project-{base}"
                owned_id = f"project-owned-{base}"
                needs_basic = gap.gap_type in {
                    "required_skill_missing",
                    "bonus_skill_missing",
                } and gap.target_level not in {None, "unknown", "basic"}
                if needs_basic:
                    band = cls._cost_band(
                        gap,
                        base=8.0,
                        target_level="basic",
                        confidence=0.25,
                        basis="skill-foundation-learning",
                    )
                    output.append(
                        WhatIfAction(
                            action_id=basic_id,
                            action_type="add_skill",
                            skill_id=gap.skill_id,
                            target_level="basic",
                            target_requirement_ids=targets,
                            estimated_hours=band.expected_hours,
                            cost_band=band,
                            stage="foundation",
                            cost_model="cost-band.v1",
                            milestone_status="planned",
                            deliverable=f"完成 {skill_name} 基础学习并保留可核验记录",
                            acceptance_criteria=(
                                "基础练习可复现",
                                "有学习/练习记录",
                            ),
                        )
                    )
                band = cls._cost_band(
                    gap,
                    base=12.0,
                    current_level="basic" if needs_basic else None,
                    confidence=0.25,
                    basis="skill-proficiency-learning",
                )
                output.append(
                    WhatIfAction(
                        action_id=learn_id,
                        action_type="add_skill",
                        skill_id=gap.skill_id,
                        target_level=gap.target_level or "working",
                        target_requirement_ids=targets,
                        estimated_hours=band.expected_hours,
                        cost_band=band,
                        stage="proficiency",
                        cost_model="cost-band.v1",
                        milestone_status="planned",
                        deliverable=f"{skill_name} 达到 {gap.target_level or 'working'} 的能力证据",
                        acceptance_criteria=(
                            "达到目标等级的能力评估",
                            "在任务中演示通过",
                        ),
                        requires_action_ids=(basic_id,) if needs_basic else (),
                    )
                )
                project_band = cls._cost_band(
                    gap,
                    base=24.0,
                    ownership="implemented",
                    confidence=0.2,
                    basis="project-experience-deliverable",
                )
                output.append(
                    WhatIfAction(
                        action_id=project_id,
                        action_type="add_project_experience",
                        skill_id=gap.skill_id,
                        target_level=gap.target_level,
                        ownership="implemented",
                        target_requirement_ids=targets,
                        responsibilities=(f"围绕 {skill_name} 完成可验收的实践任务",),
                        estimated_hours=project_band.expected_hours,
                        cost_band=project_band,
                        stage="project",
                        cost_model="cost-band.v1",
                        milestone_status="planned",
                        deliverable=f"{skill_name} 实践项目交付物",
                        acceptance_criteria=(
                            "交付物完整且可运行",
                            "验收标准明确",
                            f"对应 JD 要求 {gap.requirement_id}",
                        ),
                        requires_action_ids=(learn_id,),
                    )
                )
                owned_band = cls._cost_band(
                    gap,
                    base=32.0,
                    ownership="owned",
                    confidence=0.2,
                    basis="ownership-evidence-project",
                )
                output.append(
                    WhatIfAction(
                        action_id=owned_id,
                        action_type="add_project_experience",
                        skill_id=gap.skill_id,
                        target_level=gap.target_level,
                        ownership="owned",
                        target_requirement_ids=targets,
                        responsibilities=(f"独立负责 {skill_name} 相关模块",),
                        estimated_hours=owned_band.expected_hours,
                        cost_band=owned_band,
                        stage="ownership",
                        cost_model="cost-band.v1",
                        milestone_status="planned",
                        deliverable=f"{skill_name} 独立负责范围与成果",
                        acceptance_criteria=(
                            "独立负责范围可核验",
                            "成果与影响可溯源",
                        ),
                        requires_action_ids=(project_id,),
                    )
                )
                designed_band = cls._cost_band(
                    gap,
                    base=36.0,
                    ownership="designed",
                    confidence=0.2,
                    basis="design-ownership-evidence",
                )
                output.append(
                    WhatIfAction(
                        action_id=f"project-designed-{base}",
                        action_type="add_project_experience",
                        skill_id=gap.skill_id,
                        target_level=gap.target_level,
                        ownership="designed",
                        target_requirement_ids=targets,
                        responsibilities=(f"主导设计 {skill_name} 相关方案",),
                        estimated_hours=designed_band.expected_hours,
                        cost_band=designed_band,
                        stage="ownership",
                        cost_model="cost-band.v1",
                        milestone_status="planned",
                        deliverable=f"{skill_name} 设计方案与架构文档",
                        acceptance_criteria=(
                            "方案评审通过",
                            "设计文档可核验",
                        ),
                        requires_action_ids=(owned_id,),
                    )
                )
                evidence_band = cls._cost_band(
                    gap,
                    base=6.0,
                    confidence=0.35,
                    basis="evidence-packaging",
                )
                output.append(
                    WhatIfAction(
                        action_id=f"assessment-{base}",
                        action_type="strengthen_evidence",
                        skill_id=gap.skill_id,
                        target_level=gap.target_level or "working",
                        target_requirement_ids=targets,
                        estimated_hours=evidence_band.expected_hours,
                        cost_band=evidence_band,
                        stage="evidence",
                        cost_model="cost-band.v1",
                        milestone_status="demonstrated",
                        deliverable=f"{skill_name} 能力证据包",
                        acceptance_criteria=(
                            "证据可溯源",
                            "覆盖真实使用场景",
                        ),
                    )
                )
            elif gap.skill_id and gap.gap_type in {"evidence_gap", "usage_evidence_gap"}:
                band = cls._cost_band(
                    gap,
                    base=6.0,
                    confidence=0.35,
                    basis="evidence-packaging",
                )
                output.append(
                    WhatIfAction(
                        action_id=f"evidence-{base}",
                        action_type="strengthen_evidence",
                        skill_id=gap.skill_id,
                        target_level=gap.current_level or gap.target_level,
                        target_requirement_ids=targets,
                        estimated_hours=band.expected_hours,
                        cost_band=band,
                        stage="evidence",
                        cost_model="cost-band.v1",
                        milestone_status="demonstrated",
                        deliverable=f"{gap.skill_id} 能力证据包",
                        acceptance_criteria=(
                            "证据可溯源",
                            "覆盖真实使用场景",
                        ),
                    )
                )
            elif gap.skill_id and gap.gap_type == "ownership_gap":
                band = cls._cost_band(
                    gap,
                    base=16.0,
                    ownership=gap.target_ownership or "owned",
                    confidence=0.25,
                    basis="ownership-evidence-packaging",
                )
                output.append(
                    WhatIfAction(
                        action_id=f"ownership-{base}",
                        action_type="strengthen_ownership",
                        skill_id=gap.skill_id,
                        ownership=gap.target_ownership or "owned",
                        target_requirement_ids=targets,
                        estimated_hours=band.expected_hours,
                        cost_band=band,
                        stage="ownership",
                        cost_model="cost-band.v1",
                        milestone_status="demonstrated",
                        deliverable=f"{gap.skill_id} 独立负责证据",
                        acceptance_criteria=(
                            "职责范围与影响可核验",
                            "达到目标 ownership 等级",
                        ),
                    )
                )
            elif gap.gap_type == "scenario_gap":
                band = cls._cost_band(
                    gap,
                    base=16.0,
                    confidence=0.25,
                    basis="applied-context-evidence",
                )
                output.append(
                    WhatIfAction(
                        action_id=f"context-{base}",
                        action_type="add_project_experience",
                        target_requirement_ids=targets,
                        responsibilities=("在真实项目/工作场景中应用目标能力",),
                        business_scenarios=tuple(
                            position.business_scenarios.values[:1]
                        ),
                        estimated_hours=band.expected_hours,
                        cost_band=band,
                        stage="context",
                        cost_model="cost-band.v1",
                        milestone_status="planned",
                        deliverable="应用场景实践交付物",
                        acceptance_criteria=(
                            "场景证据可溯源",
                            "交付物可验收",
                        ),
                    )
                )
            elif gap.gap_type in {"responsibility_gap", "project_gap"} and (
                gap.skill_id
                or (responsibility is not None and responsibility.skill_ids)
            ):
                # The representative responsibility is the formal target.
                # Topic-level skill associations can contain several unrelated
                # skills, so selecting the first UUID would create arbitrary
                # advice (for example, C for an Agentic-AI responsibility).
                action_responsibilities = (
                    (responsibility.text,)
                    if responsibility is not None
                    else ("在真实项目/工作场景中应用目标能力",)
                )
                template = responsibility_action_template(
                    responsibility.text
                    if responsibility is not None
                    else action_responsibilities[0]
                )
                has_related_foundation = bool(
                    responsibility is not None
                    and any(
                        (result := skill_results_by_id.get(skill_id)) is not None
                        and result.match_status in {"matched", "partial", "weak", "declared_only"}
                        and bool(result.candidate_evidence)
                        for skill_id in responsibility.skill_ids
                    )
                )
                band = cls._cost_band(
                    gap,
                    base=template.base_hours * (0.65 if has_related_foundation else 1.0),
                    high_factor=1.5,
                    confidence=0.4 if has_related_foundation else 0.3,
                    basis=(
                        "candidate-grounded-responsibility-practice"
                        if has_related_foundation
                        else "responsibility-practice-deliverable"
                    ),
                )
                output.append(
                    WhatIfAction(
                        action_id=f"context-{base}",
                        action_type="add_project_experience",
                        skill_id=gap.skill_id,
                        canonical_name=template.name,
                        target_requirement_ids=targets,
                        responsibilities=action_responsibilities,
                        estimated_hours=band.expected_hours,
                        cost_band=band,
                        stage="context",
                        cost_model="cost-band.v1",
                        milestone_status="planned",
                        deliverable=(
                            f"基于已有相关能力，{template.deliverable}"
                            if has_related_foundation
                            else template.deliverable
                        ),
                        acceptance_criteria=template.acceptance_criteria,
                    )
                )
            if output:
                groups.append(tuple(output))
        return tuple(groups)

    @staticmethod
    def _select_actions(
        groups: tuple[tuple[WhatIfAction, ...], ...],
        max_actions: int,
    ) -> tuple[WhatIfAction, ...]:
        """Round-robin with lightweight per-type quotas.

        No single action type may occupy the whole candidate pool: direct
        skills, evidence/project, ownership and controlled transfer each keep
        reserved slots, and slots are only consumed when a candidate exists.
        """
        selected: list[WhatIfAction] = []
        seen: set[str] = set()
        counts = {bucket: 0 for bucket in _BUCKET_CAPS}
        for index in range(max((len(group) for group in groups), default=0)):
            for group in groups:
                if index >= len(group):
                    continue
                action = group[index]
                if action.action_id in seen:
                    continue
                bucket = _ACTION_TYPE_BUCKET[action.action_type]
                if counts[bucket] >= _BUCKET_CAPS[bucket]:
                    continue
                selected.append(action)
                seen.add(action.action_id)
                counts[bucket] += 1
                if len(selected) >= max_actions:
                    return tuple(selected)
        return tuple(selected)

    @staticmethod
    def _select_planning_gaps(
        gaps: tuple[PrioritizedGap, ...],
        max_bonus_gaps: int = 3,
    ) -> tuple[PrioritizedGap, ...]:
        """Pre-select bonus-scoring gaps by importance before running what-if.

        Required/context gaps are always kept; only the most important bonus
        gaps (by priority) generate candidate actions, so a large bonus list
        never floods the planner with low-value scenarios.
        """
        required = tuple(
            gap for gap in gaps if gap.gap_type != "bonus_skill_missing"
        )
        bonus = sorted(
            (gap for gap in gaps if gap.gap_type == "bonus_skill_missing"),
            key=lambda gap: (-(gap.priority_score or 0.0), gap.requirement_id),
        )
        return (*required, *bonus[:max_bonus_gaps])

    @staticmethod
    def _prefer_high_value_bonus_actions(
        actions: tuple[WhatIfAction, ...],
        gaps: tuple[PrioritizedGap, ...],
    ) -> tuple[WhatIfAction, ...]:
        """Keep at most the two most valuable bonus-scoring actions.

        Bonus skills (可加分能力) follow the same learning logic as required
        skills, but the final recommendation keeps only the most important and
        highest-gain one or two targets instead of spreading across all bonus
        skills.
        """
        bonus_requirement_ids = frozenset(
            gap.requirement_id
            for gap in gaps
            if gap.gap_type == "bonus_skill_missing"
        )
        if not bonus_requirement_ids:
            return actions
        bonus_actions = [
            item
            for item in actions
            if item.target_requirement_ids
            and item.target_requirement_ids[0] in bonus_requirement_ids
        ]
        if len(bonus_actions) <= 2:
            return actions
        priority_by_requirement = {
            gap.requirement_id: gap.priority_score for gap in gaps
        }
        ranked = sorted(
            bonus_actions,
            key=lambda item: (
                -(priority_by_requirement.get(item.target_requirement_ids[0], 0.0) or 0.0),
                -(item.estimated_score_delta or 0.0),
                item.estimated_hours,
                item.action_id,
            ),
        )
        keep = frozenset(item.action_id for item in ranked[:2])
        return tuple(
            item
            for item in actions
            if not (
                item.target_requirement_ids
                and item.target_requirement_ids[0] in bonus_requirement_ids
            )
            or item.action_id in keep
        )

    @staticmethod
    def _prefer_high_value_transfers(
        actions: tuple[WhatIfAction, ...],
        gaps: tuple[PrioritizedGap, ...],
    ) -> tuple[WhatIfAction, ...]:
        """Keep at most the two most valuable transfer recommendations.

        Transfer candidates are ranked by the source gap priority first (the
        core skills closest to the position), then by modeled score gain, then
        by cost, so the learning path recommends the most important and
        highest-gain transferable skill (or the top two) instead of spreading
        across many targets.
        """
        transfers = [
            item
            for item in actions
            if item.action_type == "controlled_skill_transfer"
        ]
        if len(transfers) <= 2:
            return actions
        priority_by_requirement = {
            gap.requirement_id: gap.priority_score for gap in gaps
        }
        ranked = sorted(
            transfers,
            key=lambda item: (
                -(priority_by_requirement.get(item.target_requirement_ids[0], 0.0) or 0.0),
                -(item.estimated_score_delta or 0.0),
                item.estimated_hours,
                item.action_id,
            ),
        )
        keep = frozenset(item.action_id for item in ranked[:2])
        return tuple(
            item
            for item in actions
            if item.action_type != "controlled_skill_transfer" or item.action_id in keep
        )

    @staticmethod
    def _cost(actions: tuple[WhatIfAction, ...]) -> float:
        return round(sum(item.estimated_hours for item in actions), 4)

    @staticmethod
    def _reachable(result: WhatIfResult) -> bool:
        projected_recommendation = (
            result.projected_recommendation
            if result.projected_recommendation is not None
            else result.scenario_recommendation
        )
        projected_gate = (
            result.projected_hard_gate_status
            if result.projected_hard_gate_status is not None
            else result.scenario_hard_gate_status
        )
        projected_gain = (
            result.projected_score_delta
            if result.projected_score_delta is not None
            else result.score_delta
        )
        return bool(
            projected_gate in {"passed", "not_applicable"}
            and projected_recommendation in {"potential_match", "strong_match"}
            and projected_gain is not None
            and projected_gain > 0
        )

    def _fastest_key(self, item: tuple[tuple[WhatIfAction, ...], WhatIfResult]) -> tuple:
        actions, result = item
        return (
            not self._reachable(result),
            self._cost(actions),
            -(result.projected_score or result.scenario_score or 0.0),
            tuple(action.action_id for action in actions),
        )

    def _budget_key(
        self,
        item: tuple[tuple[WhatIfAction, ...], WhatIfResult],
        budget: float | None,
    ) -> tuple:
        actions, result = item
        over = budget is not None and self._cost(actions) > budget
        return (
            over,
            -(result.projected_score or result.scenario_score or 0.0),
            -(result.projected_confidence or result.scenario_confidence or 0.0),
            self._cost(actions),
            tuple(action.action_id for action in actions),
        )

    def _foundation_key(self, item: tuple[tuple[WhatIfAction, ...], WhatIfResult]) -> tuple:
        actions, result = item
        foundation_actions = sum(action.stage == "foundation" for action in actions)
        return (
            foundation_actions == 0,
            self._cost(actions),
            -(
                result.projected_score_delta
                or result.score_delta
                or 0.0
            ) / max(self._cost(actions), 1.0),
            tuple(action.action_id for action in actions),
        )

    @staticmethod
    def _prerequisites_closed(
        actions: tuple[WhatIfAction, ...],
        gaps: tuple[PrioritizedGap, ...],
        cv: CVMatchProfile,
    ) -> bool:
        selected_ids = {item.action_id for item in actions}
        if any(not set(item.requires_action_ids).issubset(selected_ids) for item in actions):
            return False
        selected_skills = {item.skill_id for item in actions if item.skill_id}
        existing_skills = {
            item.skill_id
            for item in cv.capability_profiles
            if item.skill_id and item.verification_status not in {"not_observed", "unresolved"}
        }
        requirement_ids = {
            requirement_id for action in actions for requirement_id in action.target_requirement_ids
        }
        required = {
            skill_id
            for gap in gaps
            if gap.requirement_id in requirement_ids
            for skill_id in gap.prerequisite_skill_ids
        }
        return required.issubset(selected_skills | existing_skills)

    @staticmethod
    def _remaining_blockers(
        gaps: tuple[PrioritizedGap, ...],
        actions: tuple[WhatIfAction, ...],
        result: WhatIfResult,
    ) -> tuple[str, ...]:
        covered = {
            requirement_id for action in actions for requirement_id in action.target_requirement_ids
        }
        remaining = {
            gap.requirement_id
            for gap in gaps
            if gap.priority in {"critical", "high"} and gap.requirement_id not in covered
        }
        if result.scenario_evaluation is not None:
            remaining.update(
                item.group_id
                for item in result.scenario_evaluation.requirement_group_results
                if item.is_root and item.status != "satisfied"
            )
        return tuple(sorted(remaining))
