from __future__ import annotations

from typing import Any

from app.api.matching_bff.common import (
    EVIDENCE_KEYS,
    SIDE_BY_KEY,
    SIDE_OBJECT_TYPE,
    EvidenceContext,
    _bool,
    _dict,
    _evidence_version,
    _float,
    _int,
    _list,
    _result_reference,
    _str,
    _str_list,
)

__all__ = [
    "_evidence",
    "_evidence_identity",
    "_evidence_side_index",
    "_enrich_value",
    "_semantic_retrieval_evidence",
    "_semantic_candidate",
    "_semantic_explanation",
    "_skill_semantic_candidate",
    "_hard_constraint_result",
    "_skill_result",
    "_responsibility_candidate",
    "_responsibility_result",
    "_project_result",
    "_scenario_result",
    "_requirement_group_result",
]

EvidenceIdentity = tuple[str, str, int | None, int | None, int | None]


def _evidence_identity(item: Any) -> EvidenceIdentity | None:
    value = _dict(item)
    source_id = value.get("source_id")
    quote = value.get("quote")
    if not isinstance(source_id, str) or not source_id or not isinstance(quote, str):
        return None
    start = value.get("start")
    end = value.get("end")
    occurrence_index = value.get("occurrence_index")
    return (
        source_id,
        quote,
        start if isinstance(start, int) else None,
        end if isinstance(end, int) else None,
        occurrence_index if isinstance(occurrence_index, int) else None,
    )


def _evidence_side_index(value: Any) -> dict[EvidenceIdentity, str]:
    sides_by_identity: dict[EvidenceIdentity, set[str]] = {}

    def visit(item: Any) -> None:
        if isinstance(item, list):
            for child in item:
                visit(child)
            return
        if not isinstance(item, dict):
            return
        for key, child in item.items():
            side = SIDE_BY_KEY.get(key)
            if side:
                values = child if isinstance(child, list) else [child]
                for evidence in values:
                    identity = _evidence_identity(evidence)
                    if identity is not None:
                        sides_by_identity.setdefault(identity, set()).add(side)
            if isinstance(child, (dict, list)):
                visit(child)

    visit(value)
    return {
        identity: next(iter(sides))
        for identity, sides in sides_by_identity.items()
        if len(sides) == 1
    }

def _evidence(
    item: Any,
    side: str,
    *,
    context: EvidenceContext,
) -> dict[str, Any]:
    value = _dict(item)
    source_object_type = SIDE_OBJECT_TYPE.get(side, "matching_evidence")
    if side == "candidate":
        object_id = context.snapshot_id
        source_document_id = context.cv_source_version or context.snapshot_id
    elif side == "position":
        object_id = context.position_id
        source_document_id = context.position_source_version or context.position_id
    elif side == "relation":
        object_id = context.graph_version
        source_document_id = context.graph_version
    else:
        object_id = context.evaluation_id
        source_document_id = context.evaluation_id
    fragment_id = value.get("source_id")
    fragment_id = fragment_id if isinstance(fragment_id, str) else ""
    quote = value.get("quote")
    quote = quote if isinstance(quote, str) else ""
    start = value.get("start")
    end = value.get("end")
    start = start if isinstance(start, int) and start >= 0 else None
    end = end if isinstance(end, int) and end >= 0 else None
    occurrence_index = value.get("occurrence_index")
    return {
        "source_object_type": source_object_type,
        "source_object_id": object_id,
        "source_document_id": source_document_id,
        "source_fragment_id": fragment_id,
        "quote": quote,
        "start": start,
        "end": end,
        "alignment": _str(value.get("alignment")) or "unresolved",
        "occurrence_index": (occurrence_index if isinstance(occurrence_index, int) else None),
        "version": _evidence_version(context, side),
        "result_reference": _result_reference(
            source_object_type,
            object_id,
            fragment_id,
            start,
            end,
        ),
    }


def _enrich_value(
    value: Any,
    side: str,
    *,
    context: EvidenceContext,
) -> Any:
    if isinstance(value, list):
        return [_enrich_value(item, side, context=context) for item in value]
    if not isinstance(value, dict):
        return value
    output: dict[str, Any] = {}
    for key, child in value.items():
        child_side = SIDE_BY_KEY.get(key, side if key in EVIDENCE_KEYS else "mixed")
        if key in EVIDENCE_KEYS:
            if isinstance(child, list):
                output[key] = [
                    _evidence(item, child_side, context=context)
                    if isinstance(item, dict)
                    else _enrich_value(item, child_side, context=context)
                    for item in child
                ]
            elif isinstance(child, dict):
                output[key] = _evidence(child, child_side, context=context)
            else:
                output[key] = child
            continue
        output[key] = (
            _enrich_value(child, child_side, context=context)
            if isinstance(child, (dict, list))
            else child
        )
    return output


def _semantic_retrieval_evidence(
    item: Any,
    *,
    context: EvidenceContext,
) -> dict[str, Any]:
    value = _dict(item)
    return {
        "query_fragment_id": _str(value.get("query_fragment_id")) or "",
        "candidate_fragment_id": _str(value.get("candidate_fragment_id")) or "",
        "query_fragment_type": _str(value.get("query_fragment_type")) or "",
        "candidate_fragment_type": _str(value.get("candidate_fragment_type")) or "",
        "candidate_source_id": _str(value.get("candidate_source_id")) or "",
        "similarity": _float(value.get("similarity"), 0.0) or 0.0,
        "rank": _int(value.get("rank"), 1) or 1,
        "dense_rank": _int(value.get("dense_rank")),
        "sparse_rank": _int(value.get("sparse_rank")),
        "rrf_score": _float(value.get("rrf_score"), 0.0) or 0.0,
        "retrieval_score": _float(value.get("retrieval_score")),
        "rerank_score": _float(value.get("rerank_score")),
        "final_rank": _int(value.get("final_rank")),
        "evidence_ref": _evidence(
            value.get("evidence_ref"),
            "candidate",
            context=context,
        ),
        "position_evidence_ref": _evidence(
            value.get("position_evidence_ref"),
            "position",
            context=context,
        ),
        "profile_version": _str(value.get("profile_version")),
        "embedding_model": _str(value.get("embedding_model")) or "embedding.unknown",
        "embedding_revision": _str(value.get("embedding_revision")) or "",
        "embedding_dimension": _int(value.get("embedding_dimension"), 1024) or 1024,
        "embedding_normalized": _bool(value.get("embedding_normalized"), True),
        "embedding_normalization": _str(value.get("embedding_normalization")),
        "vector_representation": _str(value.get("vector_representation")),
        "vector_similarity": _str(value.get("vector_similarity")),
        "text_derivation_version": _str(value.get("text_derivation_version")),
        "index_revision": _str(value.get("index_revision")),
        "collection": _str(value.get("collection")),
        "reranker_model_revision": _str(value.get("reranker_model_revision")),
        "retrieval_trace_id": _str(value.get("retrieval_trace_id")) or "",
    }


def _semantic_candidate(
    item: Any,
    *,
    context: EvidenceContext,
) -> dict[str, Any]:
    value = _dict(item)
    return {
        "candidate_source_id": _str(value.get("candidate_source_id")) or "",
        "score": _float(value.get("score"), 0.0) or 0.0,
        "evidence": [
            _semantic_retrieval_evidence(evidence, context=context)
            for evidence in _list(value.get("evidence"))
        ],
        "retrieval_score": _float(value.get("retrieval_score")),
        "rerank_score": _float(value.get("rerank_score")),
        "final_rank": _int(value.get("final_rank")),
        "reranker_model_revision": _str(value.get("reranker_model_revision")),
        "degraded": _bool(value.get("degraded"), False),
        "degradation_reason": _str(value.get("degradation_reason")),
    }


def _semantic_explanation(item: Any) -> dict[str, Any]:
    value = _dict(item)
    return {
        "dimension": _str(value.get("dimension")) or "skill_semantic_match",
        "match_kind": _str(value.get("match_kind")) or "semantic_related",
        "score": _float(value.get("score"), 0.0) or 0.0,
        "position_text": _str(value.get("position_text")) or "",
        "resume_evidence": _str(value.get("resume_evidence")) or "",
        "evidence_ref": _str(value.get("evidence_ref")) or "",
        "embedding_revision": _str(value.get("embedding_revision")) or "",
    }


def _skill_semantic_candidate(
    item: Any,
    *,
    context: EvidenceContext,
) -> dict[str, Any]:
    value = _dict(item)
    return {
        "requirement_id": _str(value.get("requirement_id")) or "",
        "required_skill_id": _str(value.get("required_skill_id")) or "",
        "required_skill_name": _str(value.get("required_skill_name")) or "",
        "candidate_skill_id": _str(value.get("candidate_skill_id")) or "",
        "candidate_skill_name": _str(value.get("candidate_skill_name")) or "",
        "proposed_relation_type": (_str(value.get("proposed_relation_type")) or "unknown"),
        "relation_type": _str(value.get("relation_type")) or "unknown",
        "status": _str(value.get("status")) or "unknown",
        "reason_code": _str(value.get("reason_code")) or "",
        "confidence": _float(value.get("confidence"), 0.0) or 0.0,
        "position_evidence": [
            _evidence(item, "position", context=context)
            for item in _list(value.get("position_evidence"))
        ],
        "candidate_evidence": [
            _evidence(item, "candidate", context=context)
            for item in _list(value.get("candidate_evidence"))
        ],
        "relation_evidence": [
            _evidence(item, "relation", context=context)
            for item in _list(value.get("relation_evidence"))
        ],
        "relation_source": _str(value.get("relation_source")),
        "relation_graph_version": _str(value.get("relation_graph_version")),
        "model": _str(value.get("model")) or "",
        "algorithm_version": _str(value.get("algorithm_version")) or "",
    }


def _hard_constraint_result(
    item: Any,
    *,
    context: EvidenceContext,
) -> dict[str, Any]:
    value = _dict(item)
    return {
        "requirement_id": _str(value.get("requirement_id")) or "",
        "constraint_type": _str(value.get("constraint_type")) or "education",
        "status": _str(value.get("status")) or "unknown",
        "required_value": _str(value.get("required_value")),
        "candidate_value": _str(value.get("candidate_value")),
        "position_evidence": [
            _evidence(evidence, "position", context=context)
            for evidence in _list(value.get("position_evidence"))
        ],
        "candidate_evidence": [
            _evidence(evidence, "candidate", context=context)
            for evidence in _list(value.get("candidate_evidence"))
        ],
        "reason_code": _str(value.get("reason_code")) or "",
        "confidence": _float(value.get("confidence"), 0.0) or 0.0,
    }


def _skill_result(
    item: Any,
    *,
    context: EvidenceContext,
) -> dict[str, Any]:
    value = _dict(item)
    return {
        "requirement_id": _str(value.get("requirement_id")) or "",
        "skill_id": _str(value.get("skill_id")),
        "skill_name": _str(value.get("skill_name")),
        "importance_level": _str(value.get("importance_level")) or "bonus",
        "requirement_weight": _float(value.get("requirement_weight"), 1.0) or 1.0,
        "required_level": _str(value.get("required_level")),
        "candidate_declared_level": _str(value.get("candidate_declared_level")),
        "candidate_demonstrated_level": _str(value.get("candidate_demonstrated_level")),
        "verification_status": _str(value.get("verification_status")),
        "match_status": _str(value.get("match_status")) or "unknown",
        "position_evidence": [
            _evidence(evidence, "position", context=context)
            for evidence in _list(value.get("position_evidence"))
        ],
        "candidate_evidence": [
            _evidence(evidence, "candidate", context=context)
            for evidence in _list(value.get("candidate_evidence"))
        ],
        "reason_code": _str(value.get("reason_code")) or "",
        "confidence": _float(value.get("confidence"), 0.0) or 0.0,
        "match_type": _str(value.get("match_type")) or "none",
        "related_candidate_skill_id": _str(value.get("related_candidate_skill_id")),
        "prerequisite_skill_ids": _str_list(value.get("prerequisite_skill_ids")),
        "relation_type": _str(value.get("relation_type")),
        "relation_confidence": _float(value.get("relation_confidence")),
        "relation_evidence": [
            _evidence(evidence, "relation", context=context)
            for evidence in _list(value.get("relation_evidence"))
        ],
        "relation_source": _str(value.get("relation_source")),
        "relation_graph_version": _str(value.get("relation_graph_version")),
        "transferability_score": _float(value.get("transferability_score"), 0.0) or 0.0,
        "semantic_model": _str(value.get("semantic_model")),
        "semantic_algorithm_version": _str(value.get("semantic_algorithm_version")),
        "semantic_candidate_id": _str(value.get("semantic_candidate_id")),
        "candidate_ownership": _str(value.get("candidate_ownership")),
        "required_ownership": _str(value.get("required_ownership")),
    }


def _responsibility_candidate(
    item: Any,
    *,
    context: EvidenceContext,
) -> dict[str, Any]:
    value = _dict(item)
    return {
        "experience_id": _str(value.get("experience_id")) or "",
        "text": _str(value.get("text")) or "",
        "retrieval_score": _float(value.get("retrieval_score")),
        "ce_score": _float(value.get("ce_score")),
        "threshold_margin": _float(value.get("threshold_margin")),
        "evidence_refs": [
            _evidence(evidence, "candidate", context=context)
            for evidence in _list(value.get("evidence_refs"))
        ],
    }


def _responsibility_result(
    item: Any,
    *,
    context: EvidenceContext,
) -> dict[str, Any]:
    value = _dict(item)
    return {
        "requirement_id": _str(value.get("requirement_id")) or "",
        "position_requirement": _str(value.get("position_requirement")) or "",
        "candidate_experience_id": _str(value.get("candidate_experience_id")),
        "candidate_experience": _str(value.get("candidate_experience")),
        "match_status": _str(value.get("match_status")) or "unknown",
        "status_detail": _str(value.get("status_detail")),
        "matching_rules": _str_list(value.get("matching_rules")),
        "position_evidence": [
            _evidence(evidence, "position", context=context)
            for evidence in _list(value.get("position_evidence"))
        ],
        "candidate_evidence": [
            _evidence(evidence, "candidate", context=context)
            for evidence in _list(value.get("candidate_evidence"))
        ],
        "reason_code": _str(value.get("reason_code")) or "",
        "confidence": _float(value.get("confidence"), 0.0) or 0.0,
        "match_type": _str(value.get("match_type")) or "none",
        "semantic_score": _float(value.get("semantic_score")),
        "candidate_feature_id": _str(value.get("candidate_feature_id")),
        "embedding_model": _str(value.get("embedding_model")),
        "embedding_version": _str(value.get("embedding_version")),
        "semantic_reason_code": _str(value.get("semantic_reason_code")),
        "ce_score": _float(value.get("ce_score")),
        "retrieval_score": _float(value.get("retrieval_score")),
        "threshold_margin": _float(value.get("threshold_margin")),
        "top_candidates": [
            _responsibility_candidate(candidate, context=context)
            for candidate in _list(value.get("top_candidates"))
        ],
    }


def _project_result(
    item: Any,
    *,
    context: EvidenceContext,
) -> dict[str, Any]:
    value = _dict(item)
    return {
        "requirement_id": _str(value.get("requirement_id")) or "",
        "position_requirement": _str_list(value.get("position_requirement")),
        "candidate_experience_id": _str(value.get("candidate_experience_id")),
        "candidate_experience": _str(value.get("candidate_experience")),
        "candidate_role": _str(value.get("candidate_role")),
        "candidate_tasks": _str_list(value.get("candidate_tasks")),
        "candidate_achievements": _str_list(value.get("candidate_achievements")),
        "required_skill_ids": _str_list(value.get("required_skill_ids")),
        "covered_skill_ids": _str_list(value.get("covered_skill_ids")),
        "match_status": _str(value.get("match_status")) or "unknown",
        "matching_rules": _str_list(value.get("matching_rules")),
        "position_evidence": [
            _evidence(evidence, "position", context=context)
            for evidence in _list(value.get("position_evidence"))
        ],
        "candidate_evidence": [
            _evidence(evidence, "candidate", context=context)
            for evidence in _list(value.get("candidate_evidence"))
        ],
        "reason_code": _str(value.get("reason_code")) or "",
        "confidence": _float(value.get("confidence"), 0.0) or 0.0,
        "match_type": _str(value.get("match_type")) or "none",
        "semantic_score": _float(value.get("semantic_score")),
        "candidate_feature_id": _str(value.get("candidate_feature_id")),
        "embedding_model": _str(value.get("embedding_model")),
        "embedding_version": _str(value.get("embedding_version")),
        "semantic_reason_code": _str(value.get("semantic_reason_code")),
    }


def _scenario_result(
    item: Any,
    *,
    context: EvidenceContext,
) -> dict[str, Any]:
    value = _dict(item)
    return {
        "requirement_id": _str(value.get("requirement_id")) or "",
        "scenario_type": _str(value.get("scenario_type")) or "business_scenario",
        "position_requirement": _str(value.get("position_requirement")) or "",
        "candidate_experience_id": _str(value.get("candidate_experience_id")),
        "candidate_experience": _str(value.get("candidate_experience")),
        "match_status": _str(value.get("match_status")) or "unknown",
        "matching_rules": _str_list(value.get("matching_rules")),
        "position_evidence": [
            _evidence(evidence, "position", context=context)
            for evidence in _list(value.get("position_evidence"))
        ],
        "candidate_evidence": [
            _evidence(evidence, "candidate", context=context)
            for evidence in _list(value.get("candidate_evidence"))
        ],
        "reason_code": _str(value.get("reason_code")) or "",
        "confidence": _float(value.get("confidence"), 0.0) or 0.0,
        "match_type": _str(value.get("match_type")) or "none",
        "semantic_score": _float(value.get("semantic_score")),
        "candidate_feature_id": _str(value.get("candidate_feature_id")),
        "embedding_model": _str(value.get("embedding_model")),
        "embedding_version": _str(value.get("embedding_version")),
        "semantic_reason_code": _str(value.get("semantic_reason_code")),
    }


def _requirement_group_result(
    item: Any,
    *,
    context: EvidenceContext,
) -> dict[str, Any]:
    value = _dict(item)
    return {
        "group_id": _str(value.get("group_id")) or "",
        "group_type": _str(value.get("group_type")) or "must",
        "priority": _str(value.get("priority")) or "unknown",
        "status": _str(value.get("status")) or "unresolved",
        "required_count": _int(value.get("required_count"), 0) or 0,
        "satisfied_count": _int(value.get("satisfied_count"), 0) or 0,
        "evaluable_count": _int(value.get("evaluable_count"), 0) or 0,
        "child_result_ids": _str_list(value.get("child_result_ids")),
        "covered_result_ids": _str_list(value.get("covered_result_ids")),
        "covered_dimensions": _str_list(value.get("covered_dimensions")),
        "is_root": bool(value.get("is_root", False)),
        "score": _float(value.get("score")),
        "reason_code": _str(value.get("reason_code")) or "",
        "confidence": _float(value.get("confidence"), 0.0) or 0.0,
        "position_evidence": [
            _evidence(evidence, "position", context=context)
            for evidence in _list(value.get("position_evidence"))
        ],
    }
