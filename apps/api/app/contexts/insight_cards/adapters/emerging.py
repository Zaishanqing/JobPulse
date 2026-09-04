from __future__ import annotations

from collections.abc import Mapping

from app.contexts.emerging_positions.domain import EmergingRecord
from app.contexts.evidence_independence.contracts import (
    AblationCertificate,
    EvidenceIndependenceSummary,
)
from app.contexts.insight_cards.contracts import (
    EvidenceRef,
    HumanDecision,
    InsightCardSource,
)
from app.contexts.insight_cards.application import bind_evidence_versions


EMERGING_ALGORITHM_VERSION = "emerging-position.v1"


def emerging_card_source(
    record: EmergingRecord,
    *,
    summary: EvidenceIndependenceSummary | None = None,
    certificate: AblationCertificate | None = None,
    human_decision: HumanDecision | None = None,
    insight_id: str | None = None,
    qualified: bool | None = None,
    evidence_subject_ref: str | None = None,
    evidence_versions: Mapping[str, str] | None = None,
    evidence_refs: tuple[EvidenceRef, ...] | None = None,
) -> InsightCardSource:
    """Map an EmergingRecord into the shared InsightCard source DTO.

    Evidence comes from the candidate's traceable JD ids, independent sample
    size and raw count come from the EvidenceIndependenceSummary, and the
    same JD ids are recorded as used evidence.
    """

    candidate = record.candidate
    if summary is not None and summary.subject_ref != candidate.candidate_id:
        raise ValueError(
            "summary.subject_ref must match emerging candidate_id"
        )
    if evidence_refs is None:
        evidence_refs = bind_evidence_versions(
            tuple(
                _jd_evidence_ref(jd_id)
                for jd_id in candidate.evidence_jd_ids
            ),
            evidence_versions,
        )
    status = candidate.status.value
    if status == "published":
        authority = "authoritative"
    elif status == "approved":
        authority = "reviewed"
    else:
        authority = "candidate"

    if summary is not None:
        uncertainty = summary.uncertainty_state
        reasons = tuple(summary.uncertainty_reasons)
        effective_size = summary.effective_sample_size
        raw_count = summary.raw_evidence_count
        release_refs = (summary.release_id,) if summary.release_id else ()
        evidence_algorithm_version = summary.algorithm_version
        evidence_config_hash = summary.config_hash
        coverage_status = summary.coverage_status
    else:
        uncertainty = "blocked"
        reasons = ("evidence_summary_missing",)
        effective_size = None
        raw_count = None
        release_refs = ()
        evidence_algorithm_version = ""
        evidence_config_hash = ""
        coverage_status = None

    limitations: list[str] = []
    if qualified is False:
        limitations.append("germination_assessment_not_qualified")
    if status == "rejected":
        limitations.append("emerging_definition_rejected")
    if status in ("draft", "pending_review"):
        limitations.append("emerging_definition_not_reviewed")
    if any(not ref.source_version for ref in evidence_refs):
        limitations.append("evidence_source_version_missing")

    return InsightCardSource(
        insight_id=insight_id or f"insight:emerging:{candidate.candidate_id}",
        claim_type="emerging_position",
        subject_ref=candidate.candidate_id,
        claim=(
            f"{candidate.position_name} is an emerging position "
            f"with germination_score={candidate.germination_score or 0.0:.4f}"
        ),
        algorithm_version=EMERGING_ALGORITHM_VERSION,
        algorithm_config_version=None,
        algorithm_config_hash=None,
        evidence_algorithm_version=evidence_algorithm_version,
        evidence_config_hash=evidence_config_hash,
        evidence_subject_ref=evidence_subject_ref,
        coverage_status=coverage_status,
        coverage_summary=(),
        source_coverage=None,
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
        data_refs=release_refs,
        limitations=tuple(limitations),
        evidence_summary=summary,
        certificate=certificate,
        human_decision=human_decision,
        next_action_override="publish" if status == "approved" else None,
    )


def _jd_evidence_ref(jd_id: str) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=jd_id,
        source_object_type="source_jd",
        source_object_id=jd_id,
        source_document_id=jd_id,
        source_version="",
        used=True,
    )


__all__ = [
    "EMERGING_ALGORITHM_VERSION",
    "emerging_card_source",
]
