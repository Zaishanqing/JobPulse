from __future__ import annotations

from typing import Any

from app.contexts.matching_learning.matching_service import RemoteEvaluation
from app.api.matching_bff.common import (
    _bool,
    _context,
    _dict,
    _first_float,
    _float,
    _list,
    _str,
)
from app.api.matching_bff.evaluation import _evaluation
from app.api.matching_bff.learning_path import _what_if_action

__all__ = ["what_if_data"]

def what_if_data(
    value: Any,
    evaluation: RemoteEvaluation,
) -> dict[str, Any]:
    """Translate service-native evidence into the public BFF evidence contract."""
    item = _dict(value)
    context = _context(evaluation)
    return {
        "generation_status": _str(item.get("generation_status")) or "rejected",
        "scenario_id": _str(item.get("scenario_id")) or "scenario_rejected",
        "baseline_evaluation": (
            _evaluation(_dict(item.get("baseline_evaluation")), context=context)
            if item.get("baseline_evaluation") is not None
            else None
        ),
        "scenario_evaluation": (
            _evaluation(_dict(item.get("scenario_evaluation")), context=context)
            if item.get("scenario_evaluation") is not None
            else None
        ),
        "projected_evaluation": (
            _evaluation(_dict(item.get("projected_evaluation")), context=context)
            if item.get("projected_evaluation") is not None
            else None
        ),
        "actions": [
            _what_if_action(action) for action in _list(item.get("actions"))
        ],
        "baseline_score": _float(item.get("baseline_score")),
        # Primary modeled-contract fields (modeled counterfactual re-score).
        "modeled_final_score": _first_float(
            item, ("modeled_final_score", "scenario_score")
        ),
        "modeled_score_delta": _first_float(
            item, ("modeled_score_delta", "score_delta")
        ),
        "modeled_confidence_delta": _first_float(
            item, ("modeled_confidence_delta", "confidence_delta")
        ),
        "outcome_semantics": (
            _str(item.get("outcome_semantics")) or "modeled_counterfactual"
        ),
        "observed_outcome": _bool(item.get("observed_outcome"), False),
        # Deprecated aliases kept for compatibility.
        "scenario_score": _float(item.get("scenario_score")),
        "score_delta": _float(item.get("score_delta")),
        "baseline_confidence": _float(item.get("baseline_confidence")),
        "scenario_confidence": _float(item.get("scenario_confidence")),
        "confidence_delta": _float(item.get("confidence_delta")),
        "baseline_recommendation": _str(item.get("baseline_recommendation")),
        "scenario_recommendation": _str(item.get("scenario_recommendation")),
        "baseline_hard_gate_status": _str(item.get("baseline_hard_gate_status")),
        "scenario_hard_gate_status": _str(item.get("scenario_hard_gate_status")),
        "dimension_deltas": [
            {
                "dimension": _str(delta.get("dimension")) or "unknown",
                "baseline_score": _float(delta.get("baseline_score")),
                "scenario_score": _float(delta.get("scenario_score")),
                "delta": _float(delta.get("delta")),
            }
            for raw_delta in _list(item.get("dimension_deltas"))
            if (delta := _dict(raw_delta))
        ],
        "denominator_changed": _bool(item.get("denominator_changed"), False),
        "score_effect_status": _str(item.get("score_effect_status")) or "modeled",
        "baseline_evaluation_id": _str(item.get("baseline_evaluation_id")),
        "scoring_algorithm_version": _str(item.get("scoring_algorithm_version")),
        "scoring_config_version": _str(item.get("scoring_config_version")),
        "position_graph_version": _str(item.get("position_graph_version")),
        "target_type": _str(item.get("target_type")),
        "use_enterprise_weights": (
            _bool(item.get("use_enterprise_weights"), False)
            if item.get("use_enterprise_weights") is not None
            else None
        ),
        "hypothetical": True,
        "algorithm_version": _str(item.get("algorithm_version"))
        or "counterfactual-profile.v2",
        "error_code": _str(item.get("error_code")),
        "error_message": _str(item.get("error_message")),
        "projected_if_completed": _bool(
            item.get("projected_if_completed"), False
        ),
        "projected_actions": [
            _what_if_action(action)
            for action in _list(item.get("projected_actions"))
        ],
        "projected_score": _float(item.get("projected_score")),
        "projected_score_delta": _float(item.get("projected_score_delta")),
        "projected_confidence": _float(item.get("projected_confidence")),
        "projected_recommendation": _str(item.get("projected_recommendation")),
        "projected_hard_gate_status": _str(
            item.get("projected_hard_gate_status")
        ),
        "current_verified_outcome": _str(item.get("current_verified_outcome")),
        "projected_if_completed_outcome": _str(
            item.get("projected_if_completed_outcome")
        ),
    }
