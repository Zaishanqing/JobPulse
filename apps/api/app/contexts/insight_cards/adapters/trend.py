from __future__ import annotations

from collections.abc import Mapping

from app.contexts.evidence_independence.contracts import (
    AblationCertificate,
    EvidenceIndependenceSummary,
)
from app.contexts.insight_cards.application import (
    bind_evidence_versions,
    merge_uncertainty_states,
)
from app.contexts.insight_cards.contracts import (
    EvidenceRef,
    HumanDecision,
    InsightCardSource,
)
from app.contexts.market_intelligence import TrendReportRecord


TREND_REPORT_ALGORITHM_VERSION = "trend-report.v1"
MIN_TREND_SOURCE_COVERAGE = 0.6


def trend_report_card_source(
    record: TrendReportRecord,
    *,
    summary: EvidenceIndependenceSummary | None = None,
    certificate: AblationCertificate | None = None,
    human_decision: HumanDecision | None = None,
    insight_id: str | None = None,
    evidence_subject_ref: str | None = None,
    evidence_versions: Mapping[str, str] | None = None,
    evidence_refs: tuple[EvidenceRef, ...] | None = None,
) -> InsightCardSource:
    """Map a TrendReportRecord into the shared InsightCard source DTO.

    The card keeps the module's publication state as the starting authority,
    then the assembler gates it with source coverage / uncertainty and any
    attached ablation certificate.
    """

    if evidence_refs is None:
        evidence_refs = bind_evidence_versions(
            tuple(
                _document_ref(evidence_id)
                for evidence_id in record.evidence_references
            ),
            evidence_versions,
        )
    if summary is not None and summary.subject_ref != record.position_id:
        raise ValueError(
            "summary.subject_ref must match trend position_id"
        )
    status = record.status
    if status == "published":
        authority = "authoritative"
    elif status in ("reviewed", "approved"):
        authority = "reviewed"
    else:
        authority = "candidate"

    business_uncertainty: str
    business_reasons: tuple[str, ...]
    if (
        record.source_coverage is not None
        and record.source_coverage < MIN_TREND_SOURCE_COVERAGE
    ):
        business_uncertainty = "insufficient_evidence"
        business_reasons = ("source_coverage_below_minimum",)
    elif not record.evidence_references:
        business_uncertainty = "blocked"
        business_reasons = ("trend_report_without_evidence_refs",)
    else:
        business_uncertainty = "ok"
        business_reasons = ()
    if summary is not None:
        uncertainty, reasons = merge_uncertainty_states(
            (summary.uncertainty_state, tuple(summary.uncertainty_reasons)),
            (business_uncertainty, business_reasons),
        )
        effective_size = summary.effective_sample_size
        raw_count = summary.raw_evidence_count
        release_refs = (summary.release_id,) if summary.release_id else ()
        evidence_algorithm_version = summary.algorithm_version
        evidence_config_hash = summary.config_hash
        coverage_status = summary.coverage_status
    else:
        uncertainty = business_uncertainty
        reasons = business_reasons
        effective_size = None
        raw_count = None
        release_refs = ()
        evidence_algorithm_version = ""
        evidence_config_hash = ""
        coverage_status = "unknown"
    algorithm_version = (
        record.algorithm_version
        or record.formula_version
        or TREND_REPORT_ALGORITHM_VERSION
    )

    graph_refs = (
        (str(record.graph_version_id),)
        if record.graph_version_id is not None
        else ()
    )
    catalog_refs = (
        (record.skill_catalog_version,)
        if record.skill_catalog_version
        else ()
    )
    limitations: list[str] = []
    if status != "published":
        limitations.append("trend_report_not_published")
    limitations.extend(
        f"quality_flag:{flag}" for flag in record.quality_flags
    )
    limitations.extend(
        f"missing_source:{source}" for source in record.missing_sources
    )
    coverage_summary = [
        f"source_coverage:{record.source_coverage:.4f}"
        if record.source_coverage is not None
        else "source_coverage:unknown"
    ]
    coverage_summary.extend(
        f"missing_source:{source}" for source in record.missing_sources
    )
    coverage_summary.extend(
        f"quality_flag:{flag}" for flag in record.quality_flags
    )
    if any(not ref.source_version for ref in evidence_refs):
        limitations.append("evidence_source_version_missing")

    return InsightCardSource(
        insight_id=insight_id or f"insight:trend:{record.report_id}",
        claim_type="trend_change",
        subject_ref=record.position_id,
        claim=(
            f"position {record.position_id} trend report {record.report_id}: "
            f"{len(record.new_skills)} new, {len(record.rising_skills)} rising, "
            f"{len(record.replaced_skills)} replaced"
        ),
        algorithm_version=algorithm_version,
        algorithm_config_version=record.formula_version,
        algorithm_config_hash=None,
        evidence_algorithm_version=evidence_algorithm_version,
        evidence_config_hash=evidence_config_hash,
        evidence_subject_ref=evidence_subject_ref,
        coverage_status=coverage_status,
        coverage_summary=tuple(coverage_summary),
        source_coverage=record.source_coverage,
        authority_state=authority,
        evidence_refs=evidence_refs,
        used_evidence_ids=tuple(
            ref.evidence_id for ref in evidence_refs
        ),
        effective_sample_size=effective_size,
        raw_evidence_count=raw_count,
        uncertainty_state=uncertainty,
        uncertainty_reasons=reasons,
        release_refs=release_refs,
        graph_version_refs=graph_refs,
        catalog_refs=catalog_refs,
        data_refs=_time_window_refs(record),
        limitations=tuple(limitations),
        evidence_summary=summary,
        certificate=certificate,
        human_decision=human_decision,
    )


def _document_ref(evidence_id: str) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=evidence_id,
        source_object_type="source_document",
        source_object_id=evidence_id,
        source_document_id=evidence_id,
        source_version="",
        used=True,
    )


def _time_window_refs(record: TrendReportRecord) -> tuple[str, ...]:
    if record.time_window_start is None and record.time_window_end is None:
        return ()
    return (
        f"time_window:{record.time_window_start}:{record.time_window_end}",
    )


__all__ = [
    "MIN_TREND_SOURCE_COVERAGE",
    "TREND_REPORT_ALGORITHM_VERSION",
    "trend_report_card_source",
]
