from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from typing import Mapping, Sequence

from app.contexts.evidence_independence.application import (
    build_certificate,
    build_summary,
    text_fingerprint,
)
from app.contexts.evidence_independence.contracts import (
    AblationCertificate,
    CollectionTimeBasis,
    ConclusionRecomputePort,
    EvidenceIndependenceSummary,
    EvidenceRecord,
    IndependenceRequest,
    ROBUST_EVIDENCE_AGGREGATION_VERSION_V5,
)
from app.contexts.evidence_independence.temporal import TemporalFreshnessRules
from app.contexts.governance_feedback import (
    EvidenceRecord as GovernanceEvidenceRecord,
)


def governance_evidence_to_independence(
    records: Sequence[GovernanceEvidenceRecord],
    subject_ref: str,
    release_id: str,
    *,
    enterprise_ids: Mapping[str, str] | None = None,
    template_cluster_ids: Mapping[str, str] | None = None,
    source_versions: Mapping[str, str] | None = None,
    crawl_times: Mapping[str, datetime] | None = None,
) -> tuple[EvidenceRecord, ...]:
    """Map real governance Evidence rows into the independence graph input.

    ``crawl_times`` carries evidence_id -> crawler envelope acquisition time.
    When a proven crawler time is supplied the mapped record uses it as
    ``collected_at`` with ``collection_time_basis == "crawler_acquired"`` (the
    only provenance allowed to train source-lag delays).  Otherwise
    ``created_at`` (pipeline bookkeeping) maps to ``pipeline_observed``, and a
    row with neither keeps ``unknown``.
    """

    mapped: list[EvidenceRecord] = []
    for record in records:
        source_id = str(
            record.source_platform or record.source_name or "unknown"
        )
        quality = _bounded_quality(record.credibility_score)
        text = record.raw_text or record.title
        crawler_time = (crawl_times or {}).get(record.evidence_id)
        if crawler_time is not None:
            collected_at: datetime | None = crawler_time
            collection_time_basis = CollectionTimeBasis.CRAWLER_ACQUIRED
        elif record.created_at is not None:
            collected_at = record.created_at
            collection_time_basis = CollectionTimeBasis.PIPELINE_OBSERVED
        else:
            collected_at = None
            collection_time_basis = CollectionTimeBasis.UNKNOWN
        mapped.append(
            EvidenceRecord(
                evidence_id=record.evidence_id,
                subject_ref=subject_ref,
                source_id=source_id,
                enterprise_id=(
                    record.enterprise_id
                    or (enterprise_ids or {}).get(record.evidence_id)
                ),
                normalized_url=record.url,
                text_fingerprint=text_fingerprint(text),
                position_id=subject_ref,
                published_at=record.publish_date,
                collected_at=collected_at,
                collection_time_basis=collection_time_basis,
                template_cluster_id=(
                    record.template_cluster_id
                    or (template_cluster_ids or {}).get(record.evidence_id)
                ),
                release_id=release_id,
                resolution_status="resolved",
                quality_score=quality,
                completeness=bool(text),
                source_version=(
                    record.source_version
                    or (source_versions or {}).get(record.evidence_id)
                ),
                text=text,
            )
        )
    return tuple(mapped)


def build_evidence_context(
    records: Sequence[EvidenceRecord],
    subject_ref: str,
    release_id: str,
    *,
    coverage_status: str = "unknown",
    observation_reference_date: date | None = None,
    conclusion: ConclusionRecomputePort | None,
    pending_reasons: tuple[str, ...] = (),
    aggregation_version: str = "auto",
    temporal_rules: TemporalFreshnessRules | None = None,
) -> tuple[EvidenceIndependenceSummary | None, AblationCertificate | None]:
    """Build the deterministic summary/certificate for one business subject.

    ``aggregation_version == "auto"`` preserves the legacy business behavior.
    A real business caller that wants temporal awareness passes
    ``aggregation_version="robust-evidence-aggregation.v5"`` plus an explicit
    ``observation_reference_date`` and ``temporal_rules``.  The v5 path requires
    the explicit reference date (never wall clock) and uses the v3.2 clustering
    the temporal pipeline is calibrated against.
    """
    if not records or not release_id:
        return None, None
    algorithm_version = "evidence-independence.v3.2" if (
        aggregation_version == ROBUST_EVIDENCE_AGGREGATION_VERSION_V5
    ) else "evidence-independence.v2"
    request = IndependenceRequest(
        subject_ref=subject_ref,
        release_id=release_id,
        algorithm_version=algorithm_version,
        aggregation_version=aggregation_version,
        coverage_status=coverage_status,
        observation_reference_date=observation_reference_date,
        min_independent_clusters=3,
        min_effective_sample_size=3.0,
    )
    summary = build_summary(
        records,
        request,
        temporal_rules=temporal_rules,
    )
    certificate = build_certificate(
        records,
        request,
        conclusion=conclusion,
        temporal_rules=temporal_rules,
    )
    if pending_reasons:
        certificate = replace(
            certificate,
            certificate_status="not_applicable",
            certificate_reasons=tuple(dict.fromkeys(pending_reasons)),
        )
    return summary, certificate


def _bounded_quality(value: float) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    return max(0.0, min(1.0, float(value)))


__all__ = [
    "build_evidence_context",
    "governance_evidence_to_independence",
]
