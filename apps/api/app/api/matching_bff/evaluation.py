from __future__ import annotations

from typing import Any

from app.contexts.matching_learning.matching_service import (
    RemoteEvaluation,
    product_matching_method,
)
from app.api.matching_bff.common import (
    EvidenceContext,
    _bool,
    _context,
    _dict,
    _dimension_score,
    _float,
    _int,
    _list,
    _str,
    _str_list,
)
from app.api.matching_bff.evidence import (
    _evidence,
    _evidence_side_index,
    _hard_constraint_result,
    _project_result,
    _requirement_group_result,
    _responsibility_result,
    _scenario_result,
    _semantic_candidate,
    _semantic_explanation,
    _semantic_retrieval_evidence,
    _skill_result,
    _skill_semantic_candidate,
)
from app.api.matching_bff.gap import _gap_analysis

__all__ = [
    "_dimension_score",
    "_score_contribution",
    "_score_insight",
    "_final_match_result",
    "_evaluation_summary",
    "_evaluation",
    "matching_method_from_evaluation",
    "report_result_status",
    "enrich_report",
    "evidence_deletion_data",
]

def _score_contribution(
    item: Any,
    *,
    context: EvidenceContext,
) -> dict[str, Any]:
    value = _dict(item)
    return {
        "dimension": _str(value.get("dimension")) or "required_skills",
        "result_id": _str(value.get("result_id")) or "",
        "status": _str(value.get("status")) or "",
        "match_type": _str(value.get("match_type")),
        "reason_code": _str(value.get("reason_code")) or "",
        "score_value": _float(value.get("score_value")),
        "effective_weight": _float(value.get("effective_weight"), 0.0) or 0.0,
        "weighted_points": _float(value.get("weighted_points"), 0.0) or 0.0,
        "confidence": _float(value.get("confidence"), 0.0) or 0.0,
        "position_evidence": [
            _evidence(evidence, "position", context=context)
            for evidence in _list(value.get("position_evidence"))
        ],
        "candidate_evidence": [
            _evidence(evidence, "candidate", context=context)
            for evidence in _list(value.get("candidate_evidence"))
        ],
        "relation_evidence": [
            _evidence(evidence, "relation", context=context)
            for evidence in _list(value.get("relation_evidence"))
        ],
    }


def _score_insight(
    item: Any,
    *,
    context: EvidenceContext,
) -> dict[str, Any]:
    value = _dict(item)
    return {
        "dimension": _str(value.get("dimension")) or "required_skills",
        "result_id": _str(value.get("result_id")) or "",
        "reason_code": _str(value.get("reason_code")) or "",
        "message": _str(value.get("message")) or "",
        "evidence": [
            _evidence(evidence, "mixed", context=context)
            for evidence in _list(value.get("evidence"))
        ],
    }


def _final_match_result(
    item: Any,
    *,
    context: EvidenceContext,
) -> dict[str, Any] | None:
    value = _dict(item)
    if not value:
        return None
    return {
        "overall_score": _float(value.get("overall_score")),
        "match_confidence": _float(value.get("match_confidence"), 0.0) or 0.0,
        "recommendation_level": (
            _str(value.get("recommendation_level")) or "insufficient_information"
        ),
        "hard_gate_status": _str(value.get("hard_gate_status")) or "not_applicable",
        "dimension_scores": [
            _dimension_score(item) for item in _list(value.get("dimension_scores"))
        ],
        "score_contributions": [
            _score_contribution(item, context=context)
            for item in _list(value.get("score_contributions"))
        ],
        "strengths": [
            _score_insight(item, context=context) for item in _list(value.get("strengths"))
        ],
        "gaps": [_score_insight(item, context=context) for item in _list(value.get("gaps"))],
        "uncertain_items": [
            _score_insight(item, context=context) for item in _list(value.get("uncertain_items"))
        ],
        "explanation": _str(value.get("explanation")) or "",
        "algorithm_version": _str(value.get("algorithm_version")) or "",
        "scoring_config_version": _str(value.get("scoring_config_version")) or "",
        "cv_profile_id": _str(value.get("cv_profile_id")),
        "position_profile_id": _str(value.get("position_profile_id")),
        "input_evaluation_algorithm_version": _str(value.get("input_evaluation_algorithm_version"))
        or "",
        "source_evaluation_id": _str(value.get("source_evaluation_id")),
        "cv_taxonomy_version": _str(value.get("cv_taxonomy_version")) or "",
        "cv_derivation_version": _str(value.get("cv_derivation_version")) or "",
        "position_taxonomy_version": _str(value.get("position_taxonomy_version")) or "",
        "position_graph_version": _str(value.get("position_graph_version")) or "",
        "position_quality_snapshot_id": _str(value.get("position_quality_snapshot_id")) or "",
        "position_trend_version": _str(value.get("position_trend_version")),
        "vector_text_derivation_version": _str(value.get("vector_text_derivation_version")),
        "embedding_model": _str(value.get("embedding_model")),
        "embedding_version": _str(value.get("embedding_version")),
        "semantic_algorithm_version": _str(value.get("semantic_algorithm_version")),
        "semantic_threshold_config_version": _str(value.get("semantic_threshold_config_version")),
        "semantic_index_revision": _str(value.get("semantic_index_revision")),
        "semantic_collection": _str(value.get("semantic_collection")),
        "semantic_embedding_dimension": _int(value.get("semantic_embedding_dimension")),
        "semantic_embedding_normalized": (
            _bool(value.get("semantic_embedding_normalized"))
            if value.get("semantic_embedding_normalized") is not None
            else None
        ),
        "semantic_embedding_normalization": _str(value.get("semantic_embedding_normalization")),
        "semantic_vector_representation": _str(value.get("semantic_vector_representation")),
        "semantic_vector_similarity": _str(value.get("semantic_vector_similarity")),
        "semantic_text_derivation_version": _str(value.get("semantic_text_derivation_version")),
        "semantic_weight": _float(value.get("semantic_weight"), 0.0) or 0.0,
    }


def _evaluation_summary(item: Any) -> dict[str, Any] | None:
    value = _dict(item)
    if not value:
        return None
    return {
        "hard_constraint_pass_count": _int(value.get("hard_constraint_pass_count"), 0) or 0,
        "hard_constraint_fail_count": _int(value.get("hard_constraint_fail_count"), 0) or 0,
        "required_skill_matched_count": _int(value.get("required_skill_matched_count"), 0) or 0,
        "required_skill_missing_count": _int(value.get("required_skill_missing_count"), 0) or 0,
        "bonus_skill_matched_count": _int(value.get("bonus_skill_matched_count"), 0) or 0,
        "bonus_skill_missing_count": _int(value.get("bonus_skill_missing_count"), 0) or 0,
        "coverage_denominator_policy": _str(value.get("coverage_denominator_policy"))
        or "exclude_unknown_unresolved_and_not_required",
    }


def _evaluation(
    value: Any,
    *,
    context: EvidenceContext,
) -> dict[str, Any]:
    item = _dict(value)
    return {
        "evaluation_id": _str(item.get("evaluation_id")) or "",
        "cv_profile_id": _str(item.get("cv_profile_id")),
        "cv_profile_version": _str(item.get("cv_profile_version")),
        "position_profile_id": _str(item.get("position_profile_id")),
        "position_profile_version": _str(item.get("position_profile_version")),
        "algorithm_version": _str(item.get("algorithm_version")) or "",
        "evaluation_status": _str(item.get("evaluation_status")),
        "error_code": _str(item.get("error_code")),
        "error_message": _str(item.get("error_message")),
        "hard_constraint_results": [
            _hard_constraint_result(item, context=context)
            for item in _list(item.get("hard_constraint_results"))
        ],
        "skill_results": [
            _skill_result(item, context=context) for item in _list(item.get("skill_results"))
        ],
        "responsibility_results": [
            _responsibility_result(item, context=context)
            for item in _list(item.get("responsibility_results"))
        ],
        "project_results": [
            _project_result(item, context=context) for item in _list(item.get("project_results"))
        ],
        "scenario_results": [
            _scenario_result(item, context=context) for item in _list(item.get("scenario_results"))
        ],
        "requirement_group_results": [
            _requirement_group_result(group, context=context)
            for group in _list(item.get("requirement_group_results"))
        ],
        "required_skill_coverage": _float(item.get("required_skill_coverage")),
        "bonus_skill_coverage": _float(item.get("bonus_skill_coverage")),
        "hard_constraint_pass_rate": _float(item.get("hard_constraint_pass_rate")),
        "required_transferable_coverage": _float(item.get("required_transferable_coverage")),
        "bonus_transferable_coverage": _float(item.get("bonus_transferable_coverage")),
        "responsibility_coverage": _float(item.get("responsibility_coverage")),
        "project_coverage": _float(item.get("project_coverage")),
        "scenario_coverage": _float(item.get("scenario_coverage")),
        "input_coverage": _dict(item.get("input_coverage")),
        "vector_profile_version": _str(item.get("vector_profile_version")),
        "vector_text_derivation_version": _str(item.get("vector_text_derivation_version")),
        "embedding_model": _str(item.get("embedding_model")),
        "embedding_version": _str(item.get("embedding_version")),
        "semantic_algorithm_version": _str(item.get("semantic_algorithm_version")),
        "threshold_config_version": _str(item.get("threshold_config_version")),
        "semantic_status": _str(item.get("semantic_status")) or "disabled",
        "semantic_error_code": _str(item.get("semantic_error_code")),
        "semantic_shadow_score": _float(item.get("semantic_shadow_score")),
        "semantic_shadow_evidence": [
            _semantic_retrieval_evidence(evidence, context=context)
            for evidence in _list(item.get("semantic_shadow_evidence"))
        ],
        "semantic_candidates": [
            _semantic_candidate(candidate, context=context)
            for candidate in _list(item.get("semantic_candidates"))
        ],
        "semantic_shadow_status": _str(item.get("semantic_shadow_status")) or "disabled",
        "semantic_latency_ms": _float(item.get("semantic_latency_ms")),
        "semantic_retrieval_trace_id": _str(item.get("semantic_retrieval_trace_id")),
        "semantic_embedding_model": _str(item.get("semantic_embedding_model")),
        "semantic_embedding_revision": _str(item.get("semantic_embedding_revision")),
        "semantic_embedding_dimension": _int(item.get("semantic_embedding_dimension")),
        "semantic_embedding_normalized": (
            _bool(item.get("semantic_embedding_normalized"))
            if item.get("semantic_embedding_normalized") is not None
            else None
        ),
        "semantic_embedding_normalization": _str(item.get("semantic_embedding_normalization")),
        "semantic_vector_representation": _str(item.get("semantic_vector_representation")),
        "semantic_vector_similarity": _str(item.get("semantic_vector_similarity")),
        "semantic_text_derivation_version": _str(item.get("semantic_text_derivation_version")),
        "semantic_index_revision": _str(item.get("semantic_index_revision")),
        "semantic_collection": _str(item.get("semantic_collection")),
        "semantic_score": _float(item.get("semantic_score")),
        "semantic_weight": _float(item.get("semantic_weight"), 0.0) or 0.0,
        "semantic_effective_weight": _float(item.get("semantic_effective_weight"), 0.0) or 0.0,
        "semantic_evidence": [
            _semantic_retrieval_evidence(evidence, context=context)
            for evidence in _list(item.get("semantic_evidence"))
        ],
        "semantic_explanations": [
            _semantic_explanation(explanation)
            for explanation in _list(item.get("semantic_explanations"))
        ],
        "semantic_target_type": _str(item.get("semantic_target_type")),
        "semantic_stale": _bool(item.get("semantic_stale"), False),
        "semantic_llm_status": _str(item.get("semantic_llm_status")) or "disabled",
        "semantic_llm_error_code": _str(item.get("semantic_llm_error_code")),
        "semantic_llm_model": _str(item.get("semantic_llm_model")),
        "semantic_llm_algorithm_version": _str(item.get("semantic_llm_algorithm_version")),
        "semantic_llm_candidates": [
            _skill_semantic_candidate(candidate, context=context)
            for candidate in _list(item.get("semantic_llm_candidates"))
        ],
        "unresolved_count": _int(item.get("unresolved_count"), 0) or 0,
        "unknown_count": _int(item.get("unknown_count"), 0) or 0,
        "summary": _evaluation_summary(item.get("summary")),
        "final_match_result": _final_match_result(
            item.get("final_match_result"),
            context=context,
        ),
    }


def matching_method_from_evaluation(evaluation: object) -> str:
    """Return the stable product-level matching mode for an enriched evaluation."""

    return product_matching_method(evaluation)




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
    raw_evaluation = _dict(item.evaluation)
    return {
        "evaluation": _evaluation(raw_evaluation, context=context),
        "gap_analysis": _gap_analysis(
            _dict(item.gap_analysis),
            context=context,
            evidence_sides=_evidence_side_index(raw_evaluation),
        ),
    }




def evidence_deletion_data(
    value: Any,
    evaluation: RemoteEvaluation,
) -> dict[str, Any]:
    item = _dict(value)
    context = _context(evaluation)
    return {
        "generation_status": _str(item.get("generation_status")) or "rejected",
        "deletion_run_id": _str(item.get("deletion_run_id")) or "deletion_rejected",
        "deletion_kind": _str(item.get("deletion_kind")),
        "deleted_evidence_source_ids": _str_list(
            item.get("deleted_evidence_source_ids")
        ),
        "critical_evidence_source_ids": _str_list(
            item.get("critical_evidence_source_ids")
        ),
        "noncritical_evidence_source_ids": _str_list(
            item.get("noncritical_evidence_source_ids")
        ),
        "explanation_factors": [
            {
                "factor_id": _str(factor.get("factor_id")) or "",
                "factor_type": _str(factor.get("factor_type")) or "unused_evidence",
                "requirement_id": _str(factor.get("requirement_id")),
                "reason_code": _str(factor.get("reason_code")) or "UNKNOWN",
                "criticality": _str(factor.get("criticality")) or "noncritical",
                "evidence_source_ids": _str_list(
                    factor.get("evidence_source_ids")
                ),
                "used_by_scorer": _bool(factor.get("used_by_scorer"), False),
                "evidence_supported": _bool(
                    factor.get("evidence_supported"), False
                ),
            }
            for raw_factor in _list(item.get("explanation_factors"))
            if (factor := _dict(raw_factor))
        ],
        "baseline_evaluation": (
            _evaluation(_dict(item.get("baseline_evaluation")), context=context)
            if item.get("baseline_evaluation") is not None
            else None
        ),
        "ablated_evaluation": (
            _evaluation(_dict(item.get("ablated_evaluation")), context=context)
            if item.get("ablated_evaluation") is not None
            else None
        ),
        "baseline_gap_analysis": (
            _gap_analysis(_dict(item.get("baseline_gap_analysis")), context=context)
            if item.get("baseline_gap_analysis") is not None
            else None
        ),
        "ablated_gap_analysis": (
            _gap_analysis(_dict(item.get("ablated_gap_analysis")), context=context)
            if item.get("ablated_gap_analysis") is not None
            else None
        ),
        "baseline_score": _float(item.get("baseline_score")),
        "ablated_score": _float(item.get("ablated_score")),
        "retained_only_score": _float(item.get("retained_only_score")),
        "score_delta": _float(item.get("score_delta")),
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
        "baseline_hard_gate_status": _str(item.get("baseline_hard_gate_status")),
        "ablated_hard_gate_status": _str(item.get("ablated_hard_gate_status")),
        "hard_gate_delta": _str(item.get("hard_gate_delta")),
        "added_gap_ids": _str_list(item.get("added_gap_ids")),
        "removed_gap_ids": _str_list(item.get("removed_gap_ids")),
        "added_action_ids": _str_list(item.get("added_action_ids")),
        "removed_action_ids": _str_list(item.get("removed_action_ids")),
        "comprehensiveness": _float(item.get("comprehensiveness")),
        "sufficiency": _float(item.get("sufficiency")),
        "unsupported_reason_rate": _float(
            item.get("unsupported_reason_rate"), 0.0
        )
        or 0.0,
        "faithfulness_status": _str(item.get("faithfulness_status"))
        or "not_applicable",
        "baseline_evaluation_id": _str(item.get("baseline_evaluation_id")),
        "cv_profile_version": _str(item.get("cv_profile_version")),
        "position_profile_version": _str(item.get("position_profile_version")),
        "scoring_algorithm_version": _str(item.get("scoring_algorithm_version")),
        "scoring_config_version": _str(item.get("scoring_config_version")),
        "classification_policy_version": _str(
            item.get("classification_policy_version")
        )
        or "explanation-factor-policy.v1",
        "stability_threshold_points": _float(
            item.get("stability_threshold_points"), 1.0
        )
        or 0.0,
        "hypothetical": _bool(item.get("hypothetical"), True),
        "algorithm_version": _str(item.get("algorithm_version"))
        or "evidence-deletion-recompute.v1",
        "error_code": _str(item.get("error_code")),
        "error_message": _str(item.get("error_message")),
    }
