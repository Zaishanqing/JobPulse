from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.contexts.evidence_independence.contracts import (
    AblationCertificate,
    CoverageStatus,
    EvidenceIndependenceSummary,
    UncertaintyState,
)


INSIGHT_CARD_VERSION = "insight-card.v1"

ClaimType = Literal[
    "emerging_position",
    "role_migration",
    "trend_change",
    "matching_what_if",
    "fact",
    "statistical_relation",
    "prediction",
    "recommendation",
    "human_decision",
]
AuthorityState = Literal["candidate", "reviewed", "authoritative"]
NextAction = Literal[
    "publish",
    "collect_evidence",
    "rerun",
    "review",
    "user_action",
    "none",
]
HumanDecisionValue = Literal["approved", "rejected"]

_VALID_CLAIM_TYPES = frozenset(
    {
        "emerging_position",
        "role_migration",
        "trend_change",
        "matching_what_if",
        "fact",
        "statistical_relation",
        "prediction",
        "recommendation",
        "human_decision",
    }
)
_VALID_AUTHORITY_STATES = frozenset(
    {"candidate", "reviewed", "authoritative"}
)
_VALID_NEXT_ACTIONS = frozenset(
    {"publish", "collect_evidence", "rerun", "review", "user_action", "none"}
)
_VALID_UNCERTAINTY_STATES = frozenset(
    {
        "ok",
        "not_observed",
        "unresolved",
        "insufficient_evidence",
        "source_concentrated",
        "stale_observation",
        "blocked",
    }
)


@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: str
    source_object_type: str
    source_object_id: str
    source_document_id: str
    source_version: str = ""
    quote: str | None = None
    location_start: int | None = None
    location_end: int | None = None
    used: bool = True

    def __post_init__(self) -> None:
        required = (
            self.evidence_id,
            self.source_object_type,
            self.source_object_id,
            self.source_document_id,
        )
        if any(not str(value).strip() for value in required):
            raise ValueError("EvidenceRef identity fields must not be empty")
        if (self.location_start is None) != (self.location_end is None):
            raise ValueError(
                "EvidenceRef location start and end must be supplied together"
            )
        if (
            self.location_start is not None
            and self.location_end is not None
            and self.location_end < self.location_start
        ):
            raise ValueError("EvidenceRef location end must not precede start")


@dataclass(frozen=True)
class HumanDecision:
    decision_id: str
    decision: HumanDecisionValue
    decided_at: str | None = None
    decided_by: str | None = None
    reason: str | None = None
    original_authority_state: AuthorityState | None = None
    bound_object_type: str | None = None
    bound_object_id: str | None = None
    release_ref: str | None = None
    graph_version_ref: str | None = None
    algorithm_version: str | None = None
    config_version: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.decision_id, str) or not self.decision_id.strip():
            raise ValueError("decision_id must be a non-empty string")
        if self.decision not in ("approved", "rejected"):
            raise ValueError("decision must be approved or rejected")
        if self.original_authority_state not in (
            None,
            "candidate",
            "reviewed",
            "authoritative",
        ):
            raise ValueError(
                "original_authority_state must be candidate, reviewed, "
                "authoritative, or None"
            )
        for field_name in (
            "bound_object_type",
            "bound_object_id",
            "release_ref",
            "graph_version_ref",
            "algorithm_version",
            "config_version",
        ):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, str) or not value.strip()
            ):
                raise ValueError(
                    f"{field_name} must be a non-empty string or None"
                )


@dataclass(frozen=True)
class SensitivityResult:
    ablation_type: str
    removed_group_id: str | None
    removed_share: float
    before_state: str
    after_state: str
    threshold_crossed: bool
    before_score: float | None = None
    after_score: float | None = None
    certificate_status: str = "not_applicable"
    fragile_factor: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.ablation_type, str) or not self.ablation_type.strip():
            raise ValueError("ablation_type must be a non-empty string")
        if isinstance(self.removed_share, bool) or not isinstance(
            self.removed_share, (int, float)
        ):
            raise ValueError("removed_share must be numeric")
        if not 0 <= self.removed_share <= 1:
            raise ValueError("removed_share must be between 0 and 1")
        for field_name in ("before_state", "after_state"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")


@dataclass(frozen=True)
class TemporalSourceLagRow:
    """Per-source acquisition-lag summary surfaced on a card (TEMP-LAG-01).

    ``valid_sample_count`` counts only proven crawler acquisition delays; the
    provenance counts make the limitation visible instead of silently mixing
    pipeline observation time into source-lag estimation.
    """

    source_id: str
    valid_sample_count: int
    median_delay_days: float | None = None
    p90_delay_days: float | None = None
    pipeline_observation_count: int = 0
    unknown_provenance_count: int = 0
    missing_publish_count: int = 0
    invalid_sample_count: int = 0


@dataclass(frozen=True)
class TemporalEvidenceSummary:
    """Backwards-compatible optional temporal evidence summary (TEMP-LAG-01).

    Populated only when an evidence aggregation ran with
    ``robust-evidence-aggregation.v5`` + ``temporal-freshness.v1``.  All fields
    are optional so legacy cards keep serializing unchanged.  The certificate is
    propagated from the SAME business aggregation that produced the card; the
    assembler never re-runs the aggregation to fill these fields.
    """

    reference_date: str | None = None
    publish_time_coverage: float | None = None
    median_market_age_days: float | None = None
    p90_market_age_days: float | None = None
    stale_evidence_ratio: float | None = None
    freshness_adjusted_neff: float | None = None
    source_lag_summary: tuple[TemporalSourceLagRow, ...] = ()
    temporal_algorithm_version: str | None = None
    temporal_reasons: tuple[str, ...] = ()
    fresh_evidence_count: int | None = None
    stale_evidence_count: int | None = None
    unknown_evidence_count: int | None = None
    time_provenance_policy: str | None = None


@dataclass(frozen=True)
class InsightCard:
    contract_version: str = INSIGHT_CARD_VERSION
    insight_id: str = ""
    claim_type: ClaimType = "fact"
    subject_ref: str = ""
    claim: str = ""
    authority_state: AuthorityState = "candidate"
    evidence_refs: tuple[EvidenceRef, ...] = ()
    counter_evidence_refs: tuple[EvidenceRef, ...] = ()
    used_evidence_ids: tuple[str, ...] = ()
    effective_sample_size: float | None = None
    raw_evidence_count: int | None = None
    uncertainty_state: UncertaintyState = "blocked"
    uncertainty_reasons: tuple[str, ...] = ()
    sensitivity_results: tuple[SensitivityResult, ...] = ()
    fragile_factor: str | None = None
    data_refs: tuple[str, ...] = ()
    release_refs: tuple[str, ...] = ()
    graph_version_refs: tuple[str, ...] = ()
    catalog_refs: tuple[str, ...] = ()
    algorithm_version: str = ""
    algorithm_config_version: str | None = None
    algorithm_config_hash: str | None = None
    evidence_algorithm_version: str = ""
    evidence_config_hash: str = ""
    evidence_subject_ref: str | None = None
    coverage_status: CoverageStatus | None = None
    coverage_summary: tuple[str, ...] = ()
    source_coverage: float | None = None
    human_decision: HumanDecision | None = None
    limitations: tuple[str, ...] = ()
    temporal_evidence: TemporalEvidenceSummary | None = None
    next_action: NextAction = "review"

    def __post_init__(self) -> None:
        required = (
            self.insight_id,
            self.subject_ref,
            self.claim,
            self.algorithm_version,
        )
        if any(not str(value).strip() for value in required):
            raise ValueError(
                "insight_id, subject_ref, claim, and algorithm_version "
                "must not be empty"
            )
        if self.claim_type not in _VALID_CLAIM_TYPES:
            raise ValueError(f"unsupported claim_type {self.claim_type!r}")
        if self.authority_state not in _VALID_AUTHORITY_STATES:
            raise ValueError(
                f"unsupported authority_state {self.authority_state!r}"
            )
        if self.uncertainty_state not in _VALID_UNCERTAINTY_STATES:
            raise ValueError(
                f"unsupported uncertainty_state {self.uncertainty_state!r}"
            )
        if self.next_action not in _VALID_NEXT_ACTIONS:
            raise ValueError(f"unsupported next_action {self.next_action!r}")
        if self.effective_sample_size is not None:
            _validate_non_negative_number(
                self.effective_sample_size, "effective_sample_size"
            )
        if self.raw_evidence_count is not None:
            if isinstance(self.raw_evidence_count, bool) or not isinstance(
                self.raw_evidence_count, int
            ):
                raise ValueError("raw_evidence_count must be an integer")
            if self.raw_evidence_count < 0:
                raise ValueError("raw_evidence_count must not be negative")
        _validate_common_fields(self)
        _validate_used_evidence(
            self.evidence_refs,
            self.counter_evidence_refs,
            self.used_evidence_ids,
        )


@dataclass(frozen=True)
class InsightCardSource:
    insight_id: str
    claim_type: ClaimType
    subject_ref: str
    claim: str
    algorithm_version: str
    algorithm_config_version: str | None = None
    algorithm_config_hash: str | None = None
    evidence_algorithm_version: str = ""
    evidence_config_hash: str = ""
    evidence_subject_ref: str | None = None
    coverage_status: CoverageStatus | None = None
    coverage_summary: tuple[str, ...] = ()
    source_coverage: float | None = None
    authority_state: AuthorityState = "candidate"
    original_authority_state: AuthorityState | None = None
    evidence_refs: tuple[EvidenceRef, ...] = ()
    counter_evidence_refs: tuple[EvidenceRef, ...] = ()
    used_evidence_ids: tuple[str, ...] = ()
    effective_sample_size: float | None = None
    raw_evidence_count: int | None = None
    uncertainty_state: UncertaintyState = "blocked"
    uncertainty_reasons: tuple[str, ...] = ()
    release_refs: tuple[str, ...] = ()
    graph_version_refs: tuple[str, ...] = ()
    catalog_refs: tuple[str, ...] = ()
    data_refs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    evidence_summary: EvidenceIndependenceSummary | None = None
    certificate: AblationCertificate | None = None
    human_decision: HumanDecision | None = None
    next_action_override: NextAction | None = None

    def __post_init__(self) -> None:
        required = (
            self.insight_id,
            self.subject_ref,
            self.claim,
            self.algorithm_version,
        )
        if any(not str(value).strip() for value in required):
            raise ValueError(
                "insight_id, subject_ref, claim, and algorithm_version "
                "must not be empty"
            )
        if self.claim_type not in _VALID_CLAIM_TYPES:
            raise ValueError(f"unsupported claim_type {self.claim_type!r}")
        if self.authority_state not in _VALID_AUTHORITY_STATES:
            raise ValueError(
                f"unsupported authority_state {self.authority_state!r}"
            )
        if self.original_authority_state not in (
            None,
            "candidate",
            "reviewed",
            "authoritative",
        ):
            raise ValueError(
                "original_authority_state must be candidate, reviewed, "
                "authoritative, or None"
            )
        if self.uncertainty_state not in _VALID_UNCERTAINTY_STATES:
            raise ValueError(
                f"unsupported uncertainty_state {self.uncertainty_state!r}"
            )
        if (
            self.next_action_override is not None
            and self.next_action_override not in _VALID_NEXT_ACTIONS
        ):
            raise ValueError(
                f"unsupported next_action_override {self.next_action_override!r}"
            )
        if self.effective_sample_size is not None:
            _validate_non_negative_number(
                self.effective_sample_size, "effective_sample_size"
            )
        if self.raw_evidence_count is not None:
            if isinstance(self.raw_evidence_count, bool) or not isinstance(
                self.raw_evidence_count, int
            ):
                raise ValueError("raw_evidence_count must be an integer")
            if self.raw_evidence_count < 0:
                raise ValueError("raw_evidence_count must not be negative")
        _validate_common_fields(self)
        _validate_used_evidence(
            self.evidence_refs,
            self.counter_evidence_refs,
            self.used_evidence_ids,
        )


def _validate_non_negative_number(value: float, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be numeric")
    if value < 0:
        raise ValueError(f"{field_name} must not be negative")


def _validate_common_fields(source_or_card: object) -> None:
    for field_name in ("algorithm_config_version", "algorithm_config_hash"):
        value = getattr(source_or_card, field_name)
        if value is not None and (
            not isinstance(value, str) or not value.strip()
        ):
            raise ValueError(f"{field_name} must be a non-empty string or None")
    for field_name in ("evidence_algorithm_version", "evidence_config_hash"):
        value = getattr(source_or_card, field_name)
        if not isinstance(value, str):
            raise ValueError(f"{field_name} must be a string")
    evidence_subject = getattr(source_or_card, "evidence_subject_ref")
    if evidence_subject is not None and not str(evidence_subject).strip():
        raise ValueError("evidence_subject_ref must not be empty")
    coverage_status = getattr(source_or_card, "coverage_status")
    if coverage_status not in (None, "covered", "unknown"):
        raise ValueError(
            "coverage_status must be covered, unknown, or None"
        )
    source_coverage = getattr(source_or_card, "source_coverage")
    if source_coverage is not None:
        _validate_non_negative_number(source_coverage, "source_coverage")
        if source_coverage > 1:
            raise ValueError("source_coverage must not exceed 1")


def _validate_used_evidence(
    evidence_refs: tuple[EvidenceRef, ...],
    counter_evidence_refs: tuple[EvidenceRef, ...],
    used_evidence_ids: tuple[str, ...],
) -> None:
    visible = {
        ref.evidence_id for ref in (*evidence_refs, *counter_evidence_refs)
    }
    if used_evidence_ids and not visible:
        raise ValueError(
            "used_evidence_ids must reference visible evidence_refs"
        )
    missing = sorted(set(used_evidence_ids) - visible)
    if missing:
        raise ValueError(
            "used_evidence_ids must be a subset of evidence_refs: "
            + ", ".join(missing)
        )


__all__ = [
    "AuthorityState",
    "ClaimType",
    "EvidenceRef",
    "HumanDecision",
    "HumanDecisionValue",
    "INSIGHT_CARD_VERSION",
    "InsightCard",
    "InsightCardSource",
    "NextAction",
    "SensitivityResult",
    "TemporalEvidenceSummary",
    "TemporalSourceLagRow",
    "UncertaintyState",
]
