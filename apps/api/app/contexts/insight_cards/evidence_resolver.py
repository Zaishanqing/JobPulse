from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.contexts.governance_feedback import (
    EvidenceRecord as GovernanceEvidenceRecord,
)
from app.contexts.insight_cards.contracts import EvidenceRef


class ClaimEvidenceUnresolved(ValueError):
    pass


class EvidenceReadPort(Protocol):
    def get(self, evidence_id: str) -> GovernanceEvidenceRecord | None: ...
    def related(
        self, object_type: str, object_id: str
    ) -> list[GovernanceEvidenceRecord]: ...


@dataclass(frozen=True)
class ResolvedClaimEvidence:
    records: tuple[GovernanceEvidenceRecord, ...]
    refs: tuple[EvidenceRef, ...]


def resolve_claim_evidence(
    evidence_read: EvidenceReadPort,
    claim_ids: tuple[str, ...],
) -> ResolvedClaimEvidence:
    """Resolve claim document IDs to formal Governance Evidence IDs.

    A claim may reference Governance evidence IDs directly or business
    document IDs such as JD IDs. Governance Evidence rows carry the business
    document as `related_object_id`; the card must use the Governance
    Evidence ID so N_eff/certificate and evidence_refs refer to the same rows.
    """

    resolved: list[GovernanceEvidenceRecord] = []
    missing: list[str] = []
    for claim_id in claim_ids:
        rows = _rows_for_claim(evidence_read, claim_id)
        if not rows:
            missing.append(claim_id)
            continue
        resolved.extend(rows)
    if missing:
        raise ClaimEvidenceUnresolved(
            "claim evidence not found: " + ", ".join(missing)
        )
    records = _dedupe_records(resolved)
    refs = tuple(_ref(record) for record in records)
    return ResolvedClaimEvidence(records=records, refs=refs)


def _rows_for_claim(
    evidence_read: EvidenceReadPort, claim_id: str
) -> list[GovernanceEvidenceRecord]:
    rows: list[GovernanceEvidenceRecord] = []
    direct = evidence_read.get(claim_id)
    if direct is not None:
        rows.append(direct)
    rows.extend(evidence_read.related("source_jd", claim_id))
    return _dedupe_records(rows)


def _dedupe_records(
    records: list[GovernanceEvidenceRecord],
) -> tuple[GovernanceEvidenceRecord, ...]:
    seen: set[str] = set()
    result: list[GovernanceEvidenceRecord] = []
    for record in records:
        if record.evidence_id not in seen:
            seen.add(record.evidence_id)
            result.append(record)
    return tuple(result)


def _ref(record: GovernanceEvidenceRecord) -> EvidenceRef:
    source_jd_id = record.source_jd_id
    if (
        source_jd_id is None
        and record.related_object_type == "source_jd"
    ):
        source_jd_id = record.related_object_id
    document_id = source_jd_id or record.evidence_id
    return EvidenceRef(
        evidence_id=record.evidence_id,
        source_object_type="source_jd",
        source_object_id=document_id,
        source_document_id=document_id,
        source_version=record.source_version or "",
        used=True,
    )


__all__ = [
    "ClaimEvidenceUnresolved",
    "EvidenceReadPort",
    "ResolvedClaimEvidence",
    "resolve_claim_evidence",
]
