from __future__ import annotations

from typing import Any

from app.api.matching_bff.common import (
    EvidenceContext,
    _bool,
    _dict,
    _dimension_score,
    _estimate_status,
    _first_float,
    _float,
    _int,
    _list,
    _str,
    _str_list,
)
from app.api.matching_bff.evidence import _evidence

__all__ = [
    "_learning_step",
    "_counterfactual_suggestion",
    "_what_if_action",
    "_learning_route",
    "_action_cost",
    "_minimal_action_set",
    "_skill_path_decision",
]

def _learning_step(
    item: Any,
    *,
    context: EvidenceContext,
) -> dict[str, Any]:
    value = _dict(item)
    return {
        "step_order": _int(value.get("step_order"), 1) or 1,
        "source_action_id": _str(value.get("source_action_id")),
        "target_skill_id": _str(value.get("target_skill_id")),
        "objective": _str(value.get("objective")) or "",
        "prerequisite_skill_ids": _str_list(value.get("prerequisite_skill_ids")),
        "basis": _str_list(value.get("basis")),
        "estimated_hours": _float(value.get("estimated_hours"), 0.0) or 0.0,
        "cost_source_type": _str(value.get("cost_source_type")) or "unknown",
        "cost_source_ref": _str(value.get("cost_source_ref")),
        "estimate_status": _estimate_status(value.get("estimate_status")),
        "cost_model": _str(value.get("cost_model")) or "gap-learning-hours.v1",
        "completion_criteria": _str_list(value.get("completion_criteria")),
        "source_requirement_ids": _str_list(value.get("source_requirement_ids")),
        "reason_codes": _str_list(value.get("reason_codes")),
        "prerequisite_states": [
            {
                "skill_id": _str(state.get("skill_id")) or "",
                "status": _str(state.get("status")) or "unknown",
                "source": _str(state.get("source")) or "unavailable",
                "evidence_refs": [
                    _evidence(evidence, "matching", context=context)
                    for evidence in _list(state.get("evidence_refs"))
                ],
            }
            for raw_state in _list(value.get("prerequisite_states"))
            if (state := _dict(raw_state))
        ],
        "planning_status": _str(value.get("planning_status")) or "ready",
        "blocked_reason_codes": _str_list(value.get("blocked_reason_codes")),
    }


def _counterfactual_suggestion(
    item: Any,
    *,
    context: EvidenceContext,
) -> dict[str, Any]:
    value = _dict(item)
    return {
        "requirement_id": _str(value.get("requirement_id")) or "",
        "skill_id": _str(value.get("skill_id")),
        "suggestion": _str(value.get("suggestion")) or "",
        "basis_evidence": [
            _evidence(evidence, "mixed", context=context)
            for evidence in _list(value.get("basis_evidence"))
        ],
    }




def _what_if_action(item: Any) -> dict[str, Any]:
    value = _dict(item)
    raw_band = _dict(value.get("cost_band"))
    cost_band = None
    if raw_band:
        cost_band = {
            "min_hours": _float(raw_band.get("min_hours"), 0.0) or 0.0,
            "expected_hours": _float(raw_band.get("expected_hours"), 0.0) or 0.0,
            "max_hours": _float(raw_band.get("max_hours"), 0.0) or 0.0,
            "confidence": _float(raw_band.get("confidence"), 0.0) or 0.0,
            "basis": _str(raw_band.get("basis")) or "unknown",
        }
    return {
        "action_id": _str(value.get("action_id")) or "",
        "action_type": _str(value.get("action_type")) or "add_skill",
        "skill_id": _str(value.get("skill_id")),
        "canonical_name": _str(value.get("canonical_name")),
        "learning_title": _str(value.get("learning_title")),
        "target_level": _str(value.get("target_level")),
        "ownership": _str(value.get("ownership")),
        "target_requirement_ids": _str_list(value.get("target_requirement_ids")),
        "responsibilities": _str_list(value.get("responsibilities")),
        "business_scenarios": _str_list(value.get("business_scenarios")),
        "path_refs": _str_list(value.get("path_refs")),
        "estimated_hours": _float(value.get("estimated_hours"), 0.0) or 0.0,
        "cost_band": cost_band,
        "stage": _str(value.get("stage")),
        "requires_action_ids": _str_list(value.get("requires_action_ids")),
        "supersedes_action_ids": _str_list(value.get("supersedes_action_ids")),
        "cost_model": _str(value.get("cost_model")) or "heuristic_level_distance.v1",
        "estimated_score_delta": _float(value.get("estimated_score_delta")),
        "estimated_utility": _float(value.get("estimated_utility")),
        "score_effect_reason": _str(value.get("score_effect_reason")),
        "milestone_status": _str(value.get("milestone_status")),
        "deliverable": _str(value.get("deliverable")),
        "acceptance_criteria": _str_list(value.get("acceptance_criteria")),
    }


def _learning_route(item: Any) -> dict[str, Any]:
    value = _dict(item)
    return {
        "route_type": _str(value.get("route_type")) or "fastest_employment",
        "action_ids": _str_list(value.get("action_ids")),
        "total_cost_hours": _float(value.get("total_cost_hours"), 0.0) or 0.0,
        "baseline_score": _float(value.get("baseline_score")),
        # Primary modeled-contract fields (modeled counterfactual re-score).
        "modeled_final_score": _first_float(
            value, ("modeled_final_score", "final_score")
        ),
        "modeled_score_delta": _first_float(
            value, ("modeled_score_delta", "projected_match_gain")
        ),
        "modeled_confidence_delta": _first_float(
            value, ("modeled_confidence_delta", "confidence_gain")
        ),
        # Deprecated aliases kept for compatibility.
        "final_score": _float(value.get("final_score")),
        "projected_match_gain": _float(value.get("projected_match_gain")),
        "confidence_gain": _float(value.get("confidence_gain")),
        "outcome_semantics": (
            _str(value.get("outcome_semantics")) or "modeled_counterfactual"
        ),
        "observed_outcome": _bool(value.get("observed_outcome"), False),
        "target_reachable": _bool(value.get("target_reachable"), False),
        "final_recommendation": _str(value.get("final_recommendation")),
        "remaining_blocker_ids": _str_list(value.get("remaining_blocker_ids")),
        "path_refs": _str_list(value.get("path_refs")),
        "action_costs": [
            _action_cost(cost)
            for cost in _list(value.get("action_costs"))
            if cost is not None
        ],
        "scenario_dimension_scores": [
            _dimension_score(item)
            for item in _list(value.get("scenario_dimension_scores"))
        ],
        "algorithm_version": _str(value.get("algorithm_version"))
        or "learning-route-enumeration.v2",
    }


def _action_cost(item: Any) -> dict[str, Any]:
    value = _dict(item)
    return {
        "action_id": _str(value.get("action_id")) or "",
        "direct_hours": _float(value.get("direct_hours"), 0.0) or 0.0,
        "dependency_hours": _float(value.get("dependency_hours"), 0.0) or 0.0,
        "total_hours": _float(value.get("total_hours"), 0.0) or 0.0,
        "difficulty": _str(value.get("difficulty")) or "low",
        "selected": _bool(value.get("selected"), False),
        "cost_model": _str(value.get("cost_model")) or "heuristic_level_distance.v1",
        "cost_source_type": _str(value.get("cost_source_type")) or "unknown",
        "cost_source_ref": _str(value.get("cost_source_ref")),
        "estimate_status": _estimate_status(value.get("estimate_status")),
    }


def _minimal_action_set(
    item: Any,
    *,
    context: EvidenceContext,
) -> dict[str, Any] | None:
    value = _dict(item)
    if not value:
        return None
    return {
        "status": _str(value.get("status")) or "unreachable",
        "source_evaluation_id": _str(value.get("source_evaluation_id")) or "",
        "scenario_id": _str(value.get("scenario_id")),
        "selected_action_ids": _str_list(value.get("selected_action_ids")),
        "deferred_action_ids": _str_list(value.get("deferred_action_ids")),
        "action_costs": [_action_cost(cost) for cost in _list(value.get("action_costs"))],
        "minimum_action_count": _int(value.get("minimum_action_count"), 0) or 0,
        "total_cost_hours": _float(value.get("total_cost_hours"), 0.0) or 0.0,
        "budget_hours": _float(value.get("budget_hours")),
        "budget_used_hours": _float(value.get("budget_used_hours"), 0.0) or 0.0,
        "budget_remaining_hours": _float(value.get("budget_remaining_hours")),
        "baseline_score": _float(value.get("baseline_score")),
        # Primary modeled-contract fields (modeled counterfactual re-score).
        "modeled_final_score": _first_float(
            value, ("modeled_final_score", "scenario_score")
        ),
        "modeled_score_delta": _first_float(
            value, ("modeled_score_delta", "score_delta")
        ),
        "modeled_confidence_delta": _first_float(
            value, ("modeled_confidence_delta", "confidence_delta")
        ),
        # Deprecated aliases kept for compatibility.
        "scenario_score": _float(value.get("scenario_score")),
        "score_delta": _float(value.get("score_delta")),
        "outcome_semantics": (
            _str(value.get("outcome_semantics")) or "modeled_counterfactual"
        ),
        "observed_outcome": _bool(value.get("observed_outcome"), False),
        "dimension_deltas": [
            {
                "dimension": _str(delta.get("dimension")) or "unknown",
                "baseline_score": _float(delta.get("baseline_score")),
                "scenario_score": _float(delta.get("scenario_score")),
                "delta": _float(delta.get("delta")),
            }
            for raw_delta in _list(value.get("dimension_deltas"))
            if (delta := _dict(raw_delta))
        ],
        "baseline_hard_gate_status": _str(value.get("baseline_hard_gate_status")),
        "scenario_hard_gate_status": _str(value.get("scenario_hard_gate_status")),
        "hard_gate_delta": _str(value.get("hard_gate_delta")),
        "target_reachable": _bool(value.get("target_reachable"), False),
        "covered_requirement_ids": _str_list(value.get("covered_requirement_ids")),
        "evidence_refs": [
            _evidence(evidence, "matching", context=context)
            for evidence in _list(value.get("evidence_refs"))
        ],
        "path_refs": _str_list(value.get("path_refs")),
        "unreachable_reason_codes": _str_list(value.get("unreachable_reason_codes")),
        "cv_profile_version": _str(value.get("cv_profile_version")),
        "position_profile_version": _str(value.get("position_profile_version")),
        "graph_version_id": _str(value.get("graph_version_id")) or "",
        "policy_version": _str(value.get("policy_version")) or "",
        "search_status": _str(value.get("search_status")) or "exact_bounded",
        "algorithm_version": _str(value.get("algorithm_version")) or "minimal-action-set.v1",
    }


def _skill_path_decision(
    item: Any,
    *,
    context: EvidenceContext,
) -> dict[str, Any]:
    value = _dict(item)
    return {
        "target_requirement_id": _str(value.get("target_requirement_id")) or "",
        "target_skill_id": _str(value.get("target_skill_id")) or "",
        "status": _str(value.get("status")) or "unreachable",
        "paths": [
            {
                "path_id": _str(path.get("path_id")) or "",
                "source_skill_id": _str(path.get("source_skill_id")) or "",
                "target_skill_id": _str(path.get("target_skill_id")) or "",
                "target_requirement_id": _str(path.get("target_requirement_id")) or "",
                "node_skill_ids": _str_list(path.get("node_skill_ids")),
                "edges": [
                    {
                        "relation_id": _str(edge.get("relation_id")) or "",
                        "source_skill_id": _str(edge.get("source_skill_id")) or "",
                        "target_skill_id": _str(edge.get("target_skill_id")) or "",
                        "relation_type": _str(edge.get("relation_type")) or "related",
                        "graph_version": _str(edge.get("graph_version")) or "",
                        "confidence": _float(edge.get("confidence"), 0.0) or 0.0,
                        "hop_number": _int(edge.get("hop_number"), 1) or 1,
                        "edge_cost_hours": _float(edge.get("edge_cost_hours"), 0.0) or 0.0,
                        "evidence_refs": [
                            _evidence(evidence, "matching", context=context)
                            for evidence in _list(edge.get("evidence_refs"))
                        ],
                    }
                    for raw_edge in _list(path.get("edges"))
                    if (edge := _dict(raw_edge))
                ],
                "hop_count": _int(path.get("hop_count"), 1) or 1,
                "total_cost_hours": _float(path.get("total_cost_hours"), 0.0) or 0.0,
                "minimum_confidence": _float(path.get("minimum_confidence"), 0.0) or 0.0,
                "effective_confidence": _float(path.get("effective_confidence"), 0.0) or 0.0,
                "outcome_status": _str(path.get("outcome_status")) or "partial",
                "graph_version_id": _str(path.get("graph_version_id")) or "",
                "cost_model": _str(path.get("cost_model")) or "heuristic_transfer_path.v1",
            }
            for raw_path in _list(value.get("paths"))
            if (path := _dict(raw_path))
        ],
        "reason_codes": _str_list(value.get("reason_codes")),
        "max_hops": _int(value.get("max_hops"), 2) or 2,
        "max_cost_hours": _float(value.get("max_cost_hours"), 0.0) or 0.0,
        "relation_whitelist": _str_list(value.get("relation_whitelist")),
        "source_status": _str(value.get("source_status")) or "unavailable",
        "algorithm_version": _str(value.get("algorithm_version")) or "controlled-skill-path.v1",
    }
