from __future__ import annotations

from typing import Any

from app.api.matching_bff.common import (
    EvidenceContext,
    _bool,
    _dict,
    _float,
    _list,
    _str,
    _str_list,
)
from app.api.matching_bff.evidence import (
    EvidenceIdentity,
    _evidence,
    _evidence_identity,
)
from app.api.matching_bff.learning_path import (
    _counterfactual_suggestion,
    _learning_route,
    _learning_step,
    _minimal_action_set,
    _skill_path_decision,
    _what_if_action,
)

__all__ = [
    "_prioritized_gap",
    "_gap_analysis",
    "enrich_learning_path_gap",
]

def _prioritized_gap(
    item: Any,
    *,
    context: EvidenceContext,
    evidence_sides: dict[EvidenceIdentity, str] | None = None,
) -> dict[str, Any]:
    value = _dict(item)
    return {
        "gap_type": _str(value.get("gap_type")) or "evidence_gap",
        "requirement_id": _str(value.get("requirement_id")) or "",
        "skill_id": _str(value.get("skill_id")),
        "current_level": _str(value.get("current_level")),
        "target_level": _str(value.get("target_level")),
        "priority": _str(value.get("priority")) or "low",
        "priority_score": _float(value.get("priority_score"), 0.0) or 0.0,
        "reason_codes": _str_list(value.get("reason_codes")),
        "evidence": [
            _evidence(
                evidence,
                (evidence_sides or {}).get(_evidence_identity(evidence), "mixed"),
                context=context,
            )
            for evidence in _list(value.get("evidence"))
        ],
        "position_evidence_present": _bool(
            value.get("position_evidence_present"), False
        ),
        "candidate_evidence_present": _bool(
            value.get("candidate_evidence_present"), False
        ),
        "source_match_type": _str(value.get("source_match_type")),
        "transferable_skill_ids": _str_list(value.get("transferable_skill_ids")),
        "transferability_score": _float(value.get("transferability_score"), 0.0) or 0.0,
        "prerequisite_skill_ids": _str_list(value.get("prerequisite_skill_ids")),
        "current_ownership": _str(value.get("current_ownership")),
        "target_ownership": _str(value.get("target_ownership")),
        "score_effect_status": _str(value.get("score_effect_status")) or "modeled",
    }




def _gap_analysis(
    value: Any,
    *,
    context: EvidenceContext,
    evidence_sides: dict[EvidenceIdentity, str] | None = None,
) -> dict[str, Any]:
    item = _dict(value)
    gaps = [
        _prioritized_gap(gap, context=context, evidence_sides=evidence_sides)
        for gap in _list(item.get("prioritized_gaps"))
    ]
    steps = [_learning_step(step, context=context) for step in _list(item.get("learning_path"))]
    suggestions = [
        _counterfactual_suggestion(suggestion, context=context)
        for suggestion in _list(item.get("counterfactual_suggestions"))
    ]
    if item.get("error_code") or item.get("generation_status") == "rejected":
        result_status = "failed"
    elif not gaps and not steps:
        result_status = "empty"
    else:
        result_status = "completed"
    return {
        "generation_status": _str(item.get("generation_status")),
        "result_status": result_status,
        "prioritized_gaps": gaps,
        "learning_path": steps,
        "counterfactual_suggestions": suggestions,
        "candidate_actions": [
            _what_if_action(action) for action in _list(item.get("candidate_actions"))
        ],
        "learning_routes": [_learning_route(route) for route in _list(item.get("learning_routes"))],
        "minimal_action_set": _minimal_action_set(item.get("minimal_action_set"), context=context),
        "skill_path_decisions": [
            _skill_path_decision(decision, context=context)
            for decision in _list(item.get("skill_path_decisions"))
        ],
        "time_budget_hours": _float(item.get("time_budget_hours")),
        "over_budget": _bool(item.get("over_budget"), False),
        "estimated_readiness": _float(item.get("estimated_readiness")),
        "algorithm_version": _str(item.get("algorithm_version")),
        "config_version": _str(item.get("config_version")),
        "gap_policy_version": _str(item.get("gap_policy_version")),
        "gap_policy_hash": _str(item.get("gap_policy_hash")),
        "source_evaluation_algorithm_version": _str(
            item.get("source_evaluation_algorithm_version")
        ),
        "source_scoring_algorithm_version": _str(item.get("source_scoring_algorithm_version")),
        "source_scoring_config_version": _str(item.get("source_scoring_config_version")),
        "semantic_algorithm_version": _str(item.get("semantic_algorithm_version")),
        "embedding_version": _str(item.get("embedding_version")),
        "error_code": _str(item.get("error_code")),
        "error_message": _str(item.get("error_message")),
    }


def _context(item: RemoteEvaluation) -> EvidenceContext:
    versions = _versions(item)
    evaluation = _dict(item.evaluation)
    final = _dict(evaluation.get("final_match_result"))
    graph_version = (
        _str(versions.get("graph_version"))
        or _str(versions.get("position_graph_version"))
        or _str(final.get("position_graph_version"))
        or ""
    )
    return EvidenceContext(
        evaluation_id=item.evaluation_id,
        resume_id=item.resume_id or "",
        snapshot_id=item.validated_cv_snapshot_id or "",
        position_id=item.position_id or "",
        graph_version=graph_version,
        cv_source_version=_str(versions.get("cv_source_version")) or "",
        position_source_version=_str(versions.get("position_source_version")) or "",
    )


def report_result_status(
    evaluation: dict[str, Any],
    gap_analysis: dict[str, Any],
) -> str:
    if (
        evaluation.get("error_code")
        or evaluation.get("evaluation_status") == "failed"
        or gap_analysis.get("error_code")
        or gap_analysis.get("generation_status") == "rejected"
    ):
        return "failed"
    final = evaluation.get("final_match_result")
    final = final if isinstance(final, dict) else {}
    if final.get("recommendation_level") == "insufficient_information":
        return "insufficient_data"
    if (
        not evaluation.get("skill_results")
        and not evaluation.get("hard_constraint_results")
        and not gap_analysis.get("prioritized_gaps")
    ):
        return "empty"
    return "completed"


def enrich_report(item: RemoteEvaluation) -> dict[str, Any]:
    context = _context(item)
    return {
        "evaluation": _evaluation(_dict(item.evaluation), context=context),
        "gap_analysis": _gap_analysis(_dict(item.gap_analysis), context=context),
    }


def enrich_learning_path_gap(
    gap_analysis: Any,
    *,
    evaluation_id: str = "",
    resume_id: str = "",
    snapshot_id: str = "",
    position_id: str = "",
    graph_version: str = "",
    cv_source_version: str = "",
    position_source_version: str = "",
) -> dict[str, Any]:
    context = EvidenceContext(
        evaluation_id=evaluation_id,
        resume_id=resume_id,
        snapshot_id=snapshot_id,
        position_id=position_id,
        graph_version=graph_version,
        cv_source_version=cv_source_version,
        position_source_version=position_source_version,
    )
    return _gap_analysis(_dict(gap_analysis), context=context)
