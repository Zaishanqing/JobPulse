from __future__ import annotations

from collections.abc import Mapping

from app.domain.json_types import MutableJsonObject
from app.contexts.evidence_independence.contracts import (
    AblationCertificate,
    EvidenceIndependenceSummary,
)
from app.contexts.insight_cards.application import merge_uncertainty_states
from app.contexts.insight_cards.contracts import (
    EvidenceRef,
    HumanDecision,
    InsightCardSource,
)


MATCHING_WHAT_IF_ALGORITHM_VERSION = "counterfactual-profile.v2"


def matching_what_if_card_source(
    result: MutableJsonObject,
    *,
    summary: EvidenceIndependenceSummary | None = None,
    certificate: AblationCertificate | None = None,
    human_decision: HumanDecision | None = None,
    insight_id: str | None = None,
    evidence_subject_ref: str | None = None,
    evidence_refs: tuple[EvidenceRef, ...] | None = None,
) -> InsightCardSource:
    """Map a Matching What-if BFF response into the shared card source.

    The card keeps the scenario recommendation as a candidate user action and
    preserves the original score, hard gate and algorithm version. Human
    decisions only change authority, never the original algorithm output.
    """

    if not isinstance(result, Mapping):
        raise ValueError("matching what-if result must be an object")
    scenario_id = str(result.get("scenario_id") or "")
    if not scenario_id:
        raise ValueError("matching what-if requires non-empty scenario_id")
    baseline_evaluation_id = str(result.get("baseline_evaluation_id") or "")
    baseline_score = _float_or_none(result.get("baseline_score"))
    scenario_score = _float_or_none(result.get("scenario_score"))
    score_delta = _float_or_none(result.get("score_delta"))
    hard_gate = str(result.get("scenario_hard_gate_status") or "")
    hard_gate_delta = str(result.get("hard_gate_delta") or "")
    generation_status = str(result.get("generation_status") or "completed")
    target_reachable = bool(result.get("target_reachable", True))

    if evidence_refs is None:
        evidence_refs = tuple(
            _ref_from_mapping(item)
            for item in _list(result.get("evidence_refs"))
        )
        if not evidence_refs and isinstance(
            result.get("scenario_evaluation"), Mapping
        ):
            evidence_refs = _evidence_from_evaluation(
                result["scenario_evaluation"]
            )

    limitations: list[str] = []
    if scenario_score is None:
        limitations.append("what_if_scenario_score_missing")
    if not target_reachable:
        limitations.append("what_if_target_unreachable")

    if generation_status == "rejected":
        business_uncertainty = "blocked"
        business_reasons = ("what_if_generation_rejected",)
    elif hard_gate and hard_gate not in ("passed", "ok", "satisfied"):
        business_uncertainty = "blocked"
        business_reasons = (f"what_if_hard_gate:{hard_gate}",)
    elif hard_gate_delta and hard_gate_delta not in ("none", "unchanged"):
        business_uncertainty = "blocked"
        business_reasons = (
            f"what_if_hard_gate_delta:{hard_gate_delta}",
        )
    elif not evidence_refs:
        business_uncertainty = "blocked"
        business_reasons = ("what_if_without_evidence_refs",)
    else:
        business_uncertainty = "ok"
        business_reasons = ()
    if summary is not None:
        uncertainty, reasons = merge_uncertainty_states(
            (summary.uncertainty_state, tuple(summary.uncertainty_reasons)),
            (business_uncertainty, business_reasons),
        )
    else:
        uncertainty = business_uncertainty
        reasons = business_reasons

    graph_refs = tuple(
        str(value)
        for value in _list(result.get("position_graph_version"))
        if value
    )
    catalog_refs = tuple(
        str(value)
        for value in _list(
            result.get("position_catalog_version")
            or result.get("position_taxonomy_version")
        )
        if value
    )
    data_refs = tuple(
        str(value)
        for value in (
            result.get("cv_profile_version"),
            result.get("position_profile_version"),
        )
        if value
    )

    if summary is not None:
        effective_size = summary.effective_sample_size
        raw_count = summary.raw_evidence_count
        release_refs = (summary.release_id,) if summary.release_id else ()
        evidence_algorithm_version = summary.algorithm_version
        evidence_config_hash = summary.config_hash
        coverage_status = summary.coverage_status
    else:
        effective_size = None
        raw_count = None
        release_refs = ()
        evidence_algorithm_version = ""
        evidence_config_hash = ""
        coverage_status = None
    algorithm_version = str(
        result.get("algorithm_version")
        or result.get("scoring_algorithm_version")
        or MATCHING_WHAT_IF_ALGORITHM_VERSION
    )

    return InsightCardSource(
        insight_id=insight_id or f"insight:matching:{scenario_id}",
        claim_type="matching_what_if",
        subject_ref=baseline_evaluation_id or scenario_id,
        claim=(
            f"what-if scenario {scenario_id} matching score "
            f"{baseline_score} -> {scenario_score} (delta {score_delta})"
        ),
        algorithm_version=algorithm_version,
        algorithm_config_version=result.get("scoring_config_version"),
        algorithm_config_hash=None,
        evidence_algorithm_version=evidence_algorithm_version,
        evidence_config_hash=evidence_config_hash,
        evidence_subject_ref=evidence_subject_ref,
        coverage_status=coverage_status,
        coverage_summary=(),
        source_coverage=None,
        authority_state="candidate",
        evidence_refs=evidence_refs,
        used_evidence_ids=tuple(
            ref.evidence_id for ref in evidence_refs if ref.used
        ),
        effective_sample_size=effective_size,
        raw_evidence_count=raw_count,
        uncertainty_state=uncertainty,
        uncertainty_reasons=reasons,
        release_refs=release_refs,
        graph_version_refs=graph_refs,
        catalog_refs=catalog_refs,
        data_refs=data_refs,
        limitations=tuple(limitations),
        evidence_summary=summary,
        certificate=certificate,
        human_decision=human_decision,
        next_action_override="user_action",
    )


def _evidence_from_evaluation(
    evaluation: Mapping[str, object],
) -> tuple[EvidenceRef, ...]:
    refs: list[EvidenceRef] = []
    for section in ("strengths", "gaps", "uncertain_items"):
        for item in _list(evaluation.get(section)):
            if not isinstance(item, Mapping):
                continue
            for evidence in _list(item.get("evidence")):
                if isinstance(evidence, Mapping):
                    refs.append(_ref_from_mapping(evidence))
    return tuple(refs)


def _ref_from_mapping(raw: Mapping[str, object]) -> EvidenceRef:
    evidence_id = str(raw["evidence_id"])
    return EvidenceRef(
        evidence_id=evidence_id,
        source_object_type=str(
            raw.get("source_object_type") or "matching_evidence"
        ),
        source_object_id=str(raw.get("source_object_id") or evidence_id),
        source_document_id=str(raw.get("source_document_id") or evidence_id),
        source_version=str(raw.get("source_version") or ""),
        quote=raw.get("quote"),
        location_start=raw.get("location_start"),
        location_end=raw.get("location_end"),
        used=bool(raw.get("used", True)),
    )


def _float_or_none(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _list(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return (value,)


__all__ = [
    "MATCHING_WHAT_IF_ALGORITHM_VERSION",
    "matching_what_if_card_source",
]
