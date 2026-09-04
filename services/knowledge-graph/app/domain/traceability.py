"""Evidence-constrained claims and human-calibrated mapping decisions.

The values in this module are deliberately persistence agnostic.  They model the
candidate and claim planes without allowing either plane to create catalog IDs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.domain.decisions import DomainRejection


ClaimKind = Literal["observed", "reviewed", "inferred_candidate"]
ClaimSourceKind = Literal["published_fact", "legacy_local"]
ReviewDecisionKind = Literal["accept", "reject", "no_match", "supersede"]


@dataclass(frozen=True)
class ClaimEvidenceRef:
    evidence_id: int
    source_id: str
    quote: str
    start: int
    end: int
    exact: bool


@dataclass(frozen=True)
class RelationClaim:
    claim_id: str
    support_id: int
    subject_id: str
    predicate: str
    object_id: str
    claim_kind: ClaimKind
    source_kind: ClaimSourceKind
    source_fact_id: str
    source_fact_version: str
    requirement_id: str
    evidence: tuple[ClaimEvidenceRef, ...]
    validation_lineage_lineage_version: str | None
    catalog_snapshot_lineage_version: str
    mapping_policy_version: str
    observed_at: str
    graph_version_id: int | None = None


@dataclass(frozen=True)
class ClaimDecision:
    accepted: bool
    lineage_version: str | None = None
    rejection: DomainRejection | None = None


def claim_lineage_version(claim: RelationClaim) -> str:
    return claim.claim_id


def decide_relation_claim(claim: RelationClaim) -> ClaimDecision:
    required = {
        "claim_id": claim.claim_id,
        "subject_id": claim.subject_id,
        "predicate": claim.predicate,
        "object_id": claim.object_id,
        "source_fact_id": claim.source_fact_id,
        "source_fact_version": claim.source_fact_version,
        "requirement_id": claim.requirement_id,
        "catalog_snapshot_lineage_version": claim.catalog_snapshot_lineage_version,
        "mapping_policy_version": claim.mapping_policy_version,
        "observed_at": claim.observed_at,
    }
    empty = tuple(name for name, value in required.items() if not value.strip())
    if empty:
        return ClaimDecision(
            False,
            rejection=DomainRejection(
                "validation",
                f"claim fields cannot be empty: {', '.join(empty)}",
                "INVALID_RELATION_CLAIM",
            ),
        )
    if claim.support_id <= 0:
        return ClaimDecision(
            False,
            rejection=DomainRejection(
                "validation",
                "relation claim requires a positive support ID",
                "INVALID_CLAIM_SUPPORT",
            ),
        )
    lineage_versions = (
        claim.catalog_snapshot_lineage_version,
        *((claim.validation_lineage_lineage_version,) if claim.validation_lineage_lineage_version else ()),
    )
    if any(not value.strip() for value in lineage_versions):
        return ClaimDecision(
            False,
            rejection=DomainRejection(
                "validation",
                "claim lineage versions must be non-empty",
                "INVALID_CLAIM_LINEAGE_VERSION",
            ),
        )
    if not claim.evidence:
        return ClaimDecision(
            False,
            rejection=DomainRejection(
                "validation",
                "relation claim requires at least one evidence reference",
                "CLAIM_EVIDENCE_REQUIRED",
            ),
        )
    if any(
        not item.exact
        or item.start < 0
        or item.end <= item.start
        or len(item.quote) != item.end - item.start
        for item in claim.evidence
    ):
        return ClaimDecision(
            False,
            rejection=DomainRejection(
                "validation",
                "authoritative claim evidence must be exact and span-consistent",
                "CLAIM_EVIDENCE_NOT_EXACT",
            ),
        )
    if claim.claim_kind == "inferred_candidate" and claim.graph_version_id is not None:
        return ClaimDecision(
            False,
            rejection=DomainRejection(
                "conflict",
                "inferred candidates cannot belong to an authoritative graph version",
                "CANDIDATE_AUTHORITY_BOUNDARY_VIOLATION",
            ),
        )
    if claim.claim_kind in {"observed", "reviewed"} and claim.graph_version_id is None:
        return ClaimDecision(
            False,
            rejection=DomainRejection(
                "validation",
                "authoritative claims require a graph version",
                "CLAIM_GRAPH_VERSION_REQUIRED",
            ),
        )
    return ClaimDecision(True, lineage_version=claim_lineage_version(claim))


@dataclass(frozen=True)
class MappingPriorityWeights:
    uncertainty: float
    graph_impact: float
    frequency: float
    source_diversity: float
    drift: float


@dataclass(frozen=True)
class MappingCandidateSignals:
    uncertainty: float
    graph_impact: float
    frequency: float
    source_diversity: float
    drift: float


@dataclass(frozen=True)
class MappingAffectedContext:
    source_fact_id: str
    requirement_id: str


@dataclass(frozen=True)
class MappingCandidate:
    candidate_id: str
    source_expression: str
    proposed_skill_id: str
    signals: MappingCandidateSignals
    model_version: str
    index_version: str
    mapping_policy_version: str
    affected_contexts: tuple[MappingAffectedContext, ...]


@dataclass(frozen=True)
class RankedMappingCandidate:
    candidate: MappingCandidate
    priority: float


@dataclass(frozen=True)
class MappingReviewDecision:
    candidate_id: str
    decision: ReviewDecisionKind
    reviewer_id: int
    reason: str
    policy_version: str
    decided_at: str
    effective_scope: str
    replacement_candidate_id: str | None = None


def rank_mapping_candidate(
    candidate: MappingCandidate, weights: MappingPriorityWeights
) -> RankedMappingCandidate:
    signal_values = (
        candidate.signals.uncertainty,
        candidate.signals.graph_impact,
        candidate.signals.frequency,
        candidate.signals.source_diversity,
        candidate.signals.drift,
    )
    weight_values = (
        weights.uncertainty,
        weights.graph_impact,
        weights.frequency,
        weights.source_diversity,
        weights.drift,
    )
    if any(value < 0 or value > 1 for value in signal_values):
        raise ValueError("mapping candidate signals must be within [0, 1]")
    if any(value < 0 for value in weight_values) or abs(sum(weight_values) - 1) > 1e-9:
        raise ValueError("mapping priority weights must be non-negative and sum to 1")
    required = (
        candidate.candidate_id,
        candidate.source_expression,
        candidate.proposed_skill_id,
        candidate.model_version,
        candidate.index_version,
        candidate.mapping_policy_version,
    )
    if any(not value.strip() for value in required):
        raise ValueError("mapping candidate identity and version fields cannot be empty")
    if not candidate.affected_contexts or any(
        not context.source_fact_id.strip() or not context.requirement_id.strip()
        for context in candidate.affected_contexts
    ):
        raise ValueError("mapping candidate must identify affected fact requirements")
    identities = {
        (context.source_fact_id, context.requirement_id)
        for context in candidate.affected_contexts
    }
    if len(identities) != len(candidate.affected_contexts):
        raise ValueError("mapping candidate affected contexts must be unique")
    priority = sum(signal * weight for signal, weight in zip(signal_values, weight_values))
    return RankedMappingCandidate(candidate, round(priority, 6))


def validate_mapping_review(decision: MappingReviewDecision) -> None:
    required = (
        decision.candidate_id,
        decision.policy_version,
        decision.decided_at,
        decision.effective_scope,
    )
    if any(not value.strip() for value in required):
        raise ValueError("mapping review identity, version, time, and scope cannot be empty")
    if not decision.reason.strip():
        raise ValueError("mapping review decision requires a reason")
    if decision.reviewer_id <= 0:
        raise ValueError("mapping review decision requires a valid reviewer")
    if decision.decision == "supersede" and not decision.replacement_candidate_id:
        raise ValueError("supersede decision requires replacement_candidate_id")
    if decision.decision != "supersede" and decision.replacement_candidate_id is not None:
        raise ValueError("only supersede decisions may identify a replacement candidate")


def observed_claim_id(graph_version_id: int, support_id: int) -> str:
    if graph_version_id <= 0 or support_id <= 0:
        raise ValueError("graph version and support IDs must be positive")
    return f"observed:{graph_version_id}:{support_id}"
