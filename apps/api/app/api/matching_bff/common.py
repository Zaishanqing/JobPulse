from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.contexts.matching_learning.matching_service import RemoteEvaluation

__all__ = [
    "EVIDENCE_KEYS",
    "SIDE_BY_KEY",
    "SIDE_OBJECT_TYPE",
    "VERSION_KEYS",
    "ALGORITHM_VERSION_KEYS",
    "DATA_VERSION_KEYS",
    "LEGACY_STATUS_ALIASES",
    "EvidenceContext",
    "_dict",
    "_list",
    "_str",
    "_str_list",
    "_estimate_status",
    "_float",
    "_first_float",
    "_int",
    "_bool",
    "_dimension_score",
    "canonical_status",
    "_versions",
    "graph_version_from_versions",
    "match_versions_response",
    "algorithm_versions_response",
    "data_versions_response",
    "_evidence_version",
    "_result_reference",
    "_context",
]

EVIDENCE_KEYS = frozenset(
    {
        "evidence",
        "evidence_refs",
        "position_evidence",
        "candidate_evidence",
        "relation_evidence",
        "basis_evidence",
    }
)
SIDE_BY_KEY = {
    "position_evidence": "position",
    "candidate_evidence": "candidate",
    "relation_evidence": "relation",
}
SIDE_OBJECT_TYPE = {
    "candidate": "validated_cv_snapshot",
    "position": "position_profile",
    "relation": "skill_relation",
}
VERSION_KEYS = (
    "schema_version",
    "profile_contract_mapping_version",
    "graph_version",
    "embedding_model",
    "embedding_version",
    "embedding_dimension",
    "vector_text_derivation_version",
    "semantic_algorithm_version",
    "semantic_threshold_version",
    "evaluation_algorithm_version",
    "scoring_algorithm_version",
    "scoring_config_version",
    "gap_algorithm_version",
    "gap_config_version",
    "semantic_index_revision",
    "target_type",
    "use_enterprise_weights",
    "generate_learning_path",
    "cv_source_version",
    "position_source_version",
    "cv_taxonomy_version",
    "position_taxonomy_version",
    "position_graph_version",
)
ALGORITHM_VERSION_KEYS = (
    "evaluation",
    "scoring",
    "scoring_config",
    "gap",
    "gap_config",
    "semantic",
)
DATA_VERSION_KEYS = (
    "cv_source",
    "position_source",
    "cv_taxonomy",
    "position_taxonomy",
    "graph",
    "embedding",
)
LEGACY_STATUS_ALIASES = {
    "completed": "succeeded",
}


@dataclass(frozen=True)
class EvidenceContext:
    evaluation_id: str = ""
    resume_id: str = ""
    snapshot_id: str = ""
    position_id: str = ""
    graph_version: str = ""
    cv_source_version: str = ""
    position_source_version: str = ""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _str(value: Any) -> str | None:
    return str(value) if value is not None else None


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if item is not None]


def _estimate_status(value: Any) -> str:
    status = _str(value)
    if status in {"verified", "estimated", "unknown"}:
        return status
    if status == "heuristic":
        return "estimated"
    return "unknown"


def _float(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _first_float(
    mapping: dict[str, Any], keys: tuple[str, ...], default: float | None = None
) -> float | None:
    """Return the first non-null float among ``keys`` (fallback chain).

    Used to keep the new ``modeled_*`` fields populated even when a legacy
    service payload only supplies the deprecated aliases.
    """
    for key in keys:
        parsed = _float(mapping.get(key))
        if parsed is not None:
            return parsed
    return default


def _int(value: Any, default: int | None = None) -> int | None:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return bool(value)


def _dimension_score(item: Any) -> dict[str, Any]:
    value = _dict(item)
    return {
        "dimension": _str(value.get("dimension")) or "required_skills",
        "score": _float(value.get("score")),
        "confidence": _float(value.get("confidence"), 0.0) or 0.0,
        "configured_weight": _float(value.get("configured_weight"), 0.0) or 0.0,
        "effective_weight": _float(value.get("effective_weight"), 0.0) or 0.0,
        "applicable_count": _int(value.get("applicable_count"), 0) or 0,
        "scored_count": _int(value.get("scored_count"), 0) or 0,
        "uncertain_count": _int(value.get("uncertain_count"), 0) or 0,
    }


def canonical_status(value: Any) -> str:
    status = _str(value) or ""
    return LEGACY_STATUS_ALIASES.get(status, status)


def _versions(item: RemoteEvaluation) -> dict[str, Any]:
    return _dict(item.versions)


def graph_version_from_versions(value: Any) -> str:
    versions = _dict(value)
    return _str(versions.get("graph_version")) or _str(versions.get("position_graph_version")) or ""


def match_versions_response(value: Any) -> dict[str, Any] | None:
    versions = _dict(value)
    if not versions:
        return None
    return {key: versions.get(key) for key in VERSION_KEYS}


def algorithm_versions_response(value: Any) -> dict[str, Any] | None:
    versions = _dict(value)
    if not versions:
        return None
    return {key: versions.get(key) for key in ALGORITHM_VERSION_KEYS}


def data_versions_response(value: Any) -> dict[str, Any] | None:
    versions = _dict(value)
    if not versions:
        return None
    return {key: versions.get(key) for key in DATA_VERSION_KEYS}


def _evidence_version(
    context: EvidenceContext,
    side: str,
) -> dict[str, Any]:
    version: dict[str, Any] = {
        "resume_id": context.resume_id or None,
        "position_id": context.position_id or None,
        "graph_version": context.graph_version or None,
        "evaluation_id": context.evaluation_id or None,
    }
    if side == "candidate":
        version["validated_cv_snapshot_id"] = context.snapshot_id or None
        version["source_cv_version_id"] = context.cv_source_version or None
    elif side == "position":
        version["source_jd_version_id"] = context.position_source_version or None
    elif side == "relation":
        version["graph_version"] = context.graph_version or None
    return version


def _result_reference(
    object_type: str,
    object_id: str,
    fragment_id: str,
    start: int | None,
    end: int | None,
) -> str:
    span = f":{start}-{end}" if start is not None and end is not None else ""
    return f"{object_type}:{object_id}#evidence:{fragment_id}{span}"



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
