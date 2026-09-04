from __future__ import annotations

from collections.abc import Mapping

from app.domain.json_types import MutableJsonObject
from app.contexts.evidence_independence.contracts import (
    AblationCertificate,
    AblationResult,
    DistributionEntry,
    EvidenceIndependenceSummary,
)
from app.contexts.insight_cards.contracts import (
    EvidenceRef,
    HumanDecision,
    InsightCardSource,
)


def evidence_ref_from_mapping(raw: str | MutableJsonObject) -> EvidenceRef:
    if isinstance(raw, str):
        return EvidenceRef(
            evidence_id=raw,
            source_object_type="source_document",
            source_object_id=raw,
            source_document_id=raw,
        )
    if not isinstance(raw, Mapping):
        raise ValueError("evidence_ref must be a string or object")
    evidence_id = str(raw["evidence_id"])
    return EvidenceRef(
        evidence_id=evidence_id,
        source_object_type=str(
            raw.get("source_object_type") or "source_document"
        ),
        source_object_id=str(raw.get("source_object_id") or evidence_id),
        source_document_id=str(raw.get("source_document_id") or evidence_id),
        source_version=str(raw.get("source_version") or ""),
        quote=raw.get("quote"),
        location_start=raw.get("location_start"),
        location_end=raw.get("location_end"),
        used=bool(raw.get("used", True)),
    )


def human_decision_from_mapping(
    raw: MutableJsonObject | None,
) -> HumanDecision | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("human_decision must be an object")
    return HumanDecision(
        decision_id=str(raw["decision_id"]),
        decision=str(raw["decision"]),
        decided_at=raw.get("decided_at"),
        decided_by=raw.get("decided_by"),
        reason=raw.get("reason"),
        original_authority_state=raw.get("original_authority_state"),
        bound_object_type=raw.get("bound_object_type"),
        bound_object_id=raw.get("bound_object_id"),
        release_ref=raw.get("release_ref"),
        graph_version_ref=raw.get("graph_version_ref"),
        algorithm_version=raw.get("algorithm_version"),
        config_version=raw.get("config_version"),
    )


def source_from_mapping(raw: MutableJsonObject) -> InsightCardSource:
    return InsightCardSource(
        insight_id=str(raw["insight_id"]),
        claim_type=str(raw["claim_type"]),
        subject_ref=str(raw["subject_ref"]),
        claim=str(raw["claim"]),
        algorithm_version=str(raw.get("algorithm_version") or ""),
        algorithm_config_version=raw.get("algorithm_config_version"),
        algorithm_config_hash=raw.get("algorithm_config_hash"),
        evidence_algorithm_version=str(
            raw.get("evidence_algorithm_version") or ""
        ),
        evidence_config_hash=str(raw.get("evidence_config_hash") or ""),
        evidence_subject_ref=raw.get("evidence_subject_ref"),
        coverage_status=raw.get("coverage_status"),
        coverage_summary=tuple(
            str(item) for item in raw.get("coverage_summary") or ()
        ),
        source_coverage=raw.get("source_coverage"),
        authority_state=str(raw.get("authority_state") or "candidate"),
        original_authority_state=raw.get("original_authority_state"),
        evidence_refs=tuple(
            evidence_ref_from_mapping(item)
            for item in raw.get("evidence_refs") or ()
        ),
        counter_evidence_refs=tuple(
            evidence_ref_from_mapping(item)
            for item in raw.get("counter_evidence_refs") or ()
        ),
        used_evidence_ids=tuple(
            str(item) for item in raw.get("used_evidence_ids") or ()
        ),
        effective_sample_size=raw.get("effective_sample_size"),
        raw_evidence_count=raw.get("raw_evidence_count"),
        uncertainty_state=str(raw.get("uncertainty_state") or "blocked"),
        uncertainty_reasons=tuple(
            str(item) for item in raw.get("uncertainty_reasons") or ()
        ),
        release_refs=tuple(str(item) for item in raw.get("release_refs") or ()),
        graph_version_refs=tuple(
            str(item) for item in raw.get("graph_version_refs") or ()
        ),
        catalog_refs=tuple(
            str(item) for item in raw.get("catalog_refs") or ()
        ),
        data_refs=tuple(str(item) for item in raw.get("data_refs") or ()),
        limitations=tuple(
            str(item) for item in raw.get("limitations") or ()
        ),
        evidence_summary=(
            summary_from_mapping(raw["evidence_summary"])
            if raw.get("evidence_summary") is not None
            else None
        ),
        certificate=(
            certificate_from_mapping(raw["certificate"])
            if raw.get("certificate") is not None
            else None
        ),
        human_decision=human_decision_from_mapping(raw.get("human_decision")),
        next_action_override=raw.get("next_action_override"),
    )


def summary_from_mapping(raw: MutableJsonObject) -> EvidenceIndependenceSummary:
    return EvidenceIndependenceSummary(
        subject_ref=str(raw.get("subject_ref") or ""),
        release_id=raw.get("release_id"),
        algorithm_version=str(raw.get("algorithm_version") or ""),
        config_hash=str(raw.get("config_hash") or ""),
        coverage_status=str(raw.get("coverage_status") or "unknown"),
        raw_evidence_count=int(raw.get("raw_evidence_count") or 0),
        independent_cluster_count=int(
            raw.get("independent_cluster_count") or 0
        ),
        effective_sample_size=float(raw.get("effective_sample_size") or 0.0),
        unresolved_ratio=float(raw.get("unresolved_ratio") or 0.0),
        evidence_ids=tuple(
            str(item) for item in raw.get("evidence_ids") or ()
        ),
        source_distribution=tuple(
            distribution_from_mapping(item)
            for item in raw.get("source_distribution") or ()
        ),
        enterprise_distribution=tuple(
            distribution_from_mapping(item)
            for item in raw.get("enterprise_distribution") or ()
        ),
        template_distribution=tuple(
            distribution_from_mapping(item)
            for item in raw.get("template_distribution") or ()
        ),
        uncertainty_state=str(raw.get("uncertainty_state") or "blocked"),
        uncertainty_reasons=tuple(
            str(item) for item in raw.get("uncertainty_reasons") or ()
        ),
    )


def certificate_from_mapping(
    raw: MutableJsonObject,
) -> AblationCertificate:
    return AblationCertificate(
        subject_ref=str(raw.get("subject_ref") or ""),
        release_id=raw.get("release_id"),
        algorithm_version=str(raw.get("algorithm_version") or ""),
        config_hash=str(raw.get("config_hash") or ""),
        conclusion_provider=raw.get("conclusion_provider"),
        baseline=(
            summary_from_mapping(raw["baseline"])
            if raw.get("baseline") is not None
            else None
        ),
        ablations=tuple(
            ablation_result_from_mapping(item)
            for item in raw.get("ablations") or ()
        ),
        certificate_status=str(
            raw.get("certificate_status") or "not_applicable"
        ),
        certificate_reasons=tuple(
            str(item) for item in raw.get("certificate_reasons") or ()
        ),
    )


def ablation_result_from_mapping(raw: MutableJsonObject) -> AblationResult:
    return AblationResult(
        ablation_type=str(raw["ablation_type"]),
        removed_group_id=raw.get("removed_group_id"),
        removed_share=float(raw.get("removed_share") or 0.0),
        removed_count=int(raw.get("removed_count") or 0),
        before_state=str(raw.get("before_state") or "blocked"),
        after_state=str(raw.get("after_state") or "blocked"),
        before_effective_sample_size=float(
            raw.get("before_effective_sample_size") or 0.0
        ),
        after_effective_sample_size=float(
            raw.get("after_effective_sample_size") or 0.0
        ),
        before_score=raw.get("before_score"),
        after_score=raw.get("after_score"),
        before_rank=raw.get("before_rank"),
        after_rank=raw.get("after_rank"),
        before_business_state=str(raw.get("before_business_state") or ""),
        after_business_state=str(raw.get("after_business_state") or ""),
        before_target_found=bool(raw.get("before_target_found", True)),
        after_target_found=bool(raw.get("after_target_found", True)),
        threshold_crossed=bool(raw.get("threshold_crossed") or False),
        state_changed=bool(raw.get("state_changed") or False),
        failure_reasons=tuple(
            str(item) for item in raw.get("failure_reasons") or ()
        ),
    )


def distribution_from_mapping(raw: MutableJsonObject) -> DistributionEntry:
    return DistributionEntry(
        group_id=str(raw["group_id"]),
        count=int(raw["count"]),
        share=float(raw["share"]),
    )


__all__ = [
    "ablation_result_from_mapping",
    "certificate_from_mapping",
    "distribution_from_mapping",
    "evidence_ref_from_mapping",
    "human_decision_from_mapping",
    "source_from_mapping",
    "summary_from_mapping",
]
