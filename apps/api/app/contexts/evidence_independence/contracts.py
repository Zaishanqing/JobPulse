from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, Mapping, Protocol, Sequence, TypeAlias, TYPE_CHECKING

if TYPE_CHECKING:
    from app.contexts.evidence_independence.temporal import (
        TemporalFreshnessCertificate,
    )


EVIDENCE_INDEPENDENCE_SUMMARY_VERSION = "evidence-independence-summary.v1"
EVIDENCE_INDEPENDENCE_ABLATION_VERSION = "evidence-independence-ablation.v2"
EVIDENCE_INDEPENDENCE_ALGORITHM_VERSION = "evidence-independence.v2"
EVIDENCE_INDEPENDENCE_ALGORITHM_VERSION_V3 = "evidence-independence.v3"
EVIDENCE_INDEPENDENCE_ALGORITHM_VERSION_V31 = "evidence-independence.v3.1"
EVIDENCE_INDEPENDENCE_ALGORITHM_VERSION_V32 = "evidence-independence.v3.2"
ROBUST_EVIDENCE_AGGREGATION_VERSION_V3 = "robust-evidence-aggregation.v3"
ROBUST_EVIDENCE_AGGREGATION_VERSION_V4 = "robust-evidence-aggregation.v4"
ROBUST_EVIDENCE_AGGREGATION_VERSION_V5 = "robust-evidence-aggregation.v5"

JsonPayload: TypeAlias = (
    dict[str, "JsonPayload"] | list["JsonPayload"] | str | int | float | bool | None
)
ContinuityCertificate: TypeAlias = dict[str, JsonPayload]

CoverageStatus = Literal["covered", "unknown"]
ResolutionStatus = Literal["resolved", "unresolved"]
AblationType = Literal["source", "enterprise", "template", "time_window"]

class CollectionTimeBasis:
    """Stable string constants for ``collected_at`` provenance.

    ``crawler_acquired`` is the only value allowed to train source-lag delay
    samples; ``pipeline_observed`` is extraction/governance/release bookkeeping
    time; ``unknown`` means the provenance cannot be confirmed.
    """

    CRAWLER_ACQUIRED = "crawler_acquired"
    PIPELINE_OBSERVED = "pipeline_observed"
    UNKNOWN = "unknown"


CollectionTimeBasisLiteral = Literal[
    "crawler_acquired",
    "pipeline_observed",
    "unknown",
]

UncertaintyState = Literal[
    "ok",
    "not_observed",
    "unresolved",
    "insufficient_evidence",
    "source_concentrated",
    "stale_observation",
    "blocked",
]
CertificateStatus = Literal[
    "robust",
    "conditionally_robust",
    "source_fragile",
    "insufficient_evidence",
    "not_applicable",
]


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    subject_ref: str
    source_id: str
    enterprise_id: str | None = None
    normalized_url: str | None = None
    text_fingerprint: str | None = None
    position_id: str | None = None
    published_at: date | None = None
    collected_at: datetime | None = None
    # Time provenance for ``collected_at`` (crawler envelope vs pipeline vs
    # unknown).  Backward-compatible: legacy callers omit it and it defaults to
    # ``"unknown"`` which is never treated as crawler acquisition.
    collection_time_basis: CollectionTimeBasisLiteral = "unknown"
    template_cluster_id: str | None = None
    release_id: str | None = None
    source_version: str | None = None
    resolution_status: ResolutionStatus = "resolved"
    quality_score: float = 1.0
    completeness: bool = True
    text: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_id, str) or not self.evidence_id.strip():
            raise ValueError("evidence_id must be a non-empty string")
        if not isinstance(self.subject_ref, str) or not self.subject_ref.strip():
            raise ValueError("subject_ref must be a non-empty string")
        if not isinstance(self.source_id, str) or not self.source_id.strip():
            raise ValueError("source_id must be a non-empty string")
        if isinstance(self.quality_score, bool) or not isinstance(
            self.quality_score, (int, float)
        ):
            raise ValueError("quality_score must be numeric")
        if not 0 <= self.quality_score <= 1:
            raise ValueError("quality_score must be between 0 and 1")
        if self.resolution_status not in ("resolved", "unresolved"):
            raise ValueError("resolution_status must be resolved or unresolved")
        if self.collection_time_basis not in (
            CollectionTimeBasis.CRAWLER_ACQUIRED,
            CollectionTimeBasis.PIPELINE_OBSERVED,
            CollectionTimeBasis.UNKNOWN,
        ):
            raise ValueError(
                "collection_time_basis must be crawler_acquired, "
                "pipeline_observed, or unknown"
            )


@dataclass(frozen=True)
class IndependenceRequest:
    subject_ref: str
    release_id: str | None = None
    algorithm_version: str = EVIDENCE_INDEPENDENCE_ALGORITHM_VERSION
    aggregation_version: str = "auto"
    coverage_status: CoverageStatus = "unknown"
    min_independent_clusters: int = 3
    min_effective_sample_size: float = 3.0
    concentration_threshold: float = 0.6
    unresolved_threshold: float = 0.3
    near_duplicate_window_days: int = 7
    stale_observation_days: int = 365
    observation_reference_date: date | None = None
    window_days: int | None = None
    ablation_window_days: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.subject_ref, str) or not self.subject_ref.strip():
            raise ValueError("subject_ref must be a non-empty string")
        if self.coverage_status not in ("covered", "unknown"):
            raise ValueError("coverage_status must be covered or unknown")
        if isinstance(self.min_independent_clusters, bool) or not isinstance(
            self.min_independent_clusters, int
        ):
            raise ValueError("min_independent_clusters must be an integer")
        if self.min_independent_clusters < 1:
            raise ValueError("min_independent_clusters must be positive")
        if self.min_effective_sample_size <= 0:
            raise ValueError("min_effective_sample_size must be positive")
        if not 0 < self.concentration_threshold <= 1:
            raise ValueError("concentration_threshold must be between 0 and 1")
        if not 0 <= self.unresolved_threshold <= 1:
            raise ValueError("unresolved_threshold must be between 0 and 1")
        if self.near_duplicate_window_days < 0:
            raise ValueError("near_duplicate_window_days must not be negative")
        if self.stale_observation_days < 1:
            raise ValueError("stale_observation_days must be positive")
        for field_name in ("window_days", "ablation_window_days"):
            value = getattr(self, field_name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{field_name} must be an integer")
            if value < 1:
                raise ValueError(f"{field_name} must be positive")
        if (
            self.window_days is not None
            and self.ablation_window_days is not None
            and self.ablation_window_days >= self.window_days
        ):
            raise ValueError(
                "ablation_window_days must be smaller than window_days"
            )
        allowed_aggregations = {
            "auto",
            "robust-evidence-aggregation.v1",
            "robust-evidence-aggregation.v2",
            "robust-evidence-aggregation.v3",
            "robust-evidence-aggregation.v4",
            "robust-evidence-aggregation.v5",
        }
        if self.aggregation_version not in allowed_aggregations:
            raise ValueError(
                "aggregation_version must be one of "
                + ", ".join(sorted(allowed_aggregations))
            )


@dataclass(frozen=True)
class ConclusionScore:
    """Deterministic downstream conclusion output for one subject dataset."""

    score: float
    state: UncertaintyState = "ok"
    rank: int | None = None
    threshold_crossed: bool = False
    failure_reasons: tuple[str, ...] = ()
    business_state: str = ""
    target_found: bool = True
    candidate_identity: str | None = None
    continuity_certificate: ContinuityCertificate | None = None


class ConclusionRecomputePort(Protocol):
    def evaluate(
        self,
        records: Sequence[EvidenceRecord],
        request: IndependenceRequest,
    ) -> ConclusionScore: ...


@dataclass(frozen=True)
class IndependenceWeightRules:
    base_weight: float = 1.0
    quality_factor: bool = True
    completeness_factor: bool = True

    def __post_init__(self) -> None:
        if self.base_weight <= 0:
            raise ValueError("base_weight must be positive")


@dataclass(frozen=True)
class SourceAwareClusteringRules:
    """V3 source-aware merge policy.

    Semantic similarity (text/entity/provenance/structure) only nominates a
    duplicate candidate.  A merge also requires cluster compatibility:
    cross-enterprise, cross-source pairs stay independent unless strong
    identity evidence (exact document fingerprint or normalized URL) exists.
    """

    text_weight: float = 0.40
    entity_weight: float = 0.25
    provenance_weight: float = 0.20
    structure_weight: float = 0.15
    independent_company_penalty: float = 0.45
    independent_source_penalty: float = 0.20
    merge_threshold: float = 0.55

    def __post_init__(self) -> None:
        for field_name in (
            "text_weight",
            "entity_weight",
            "provenance_weight",
            "structure_weight",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{field_name} must be numeric")
            if not 0 <= value <= 1:
                raise ValueError(f"{field_name} must be between 0 and 1")


@dataclass(frozen=True)
class MissingAwareScoringRules:
    """V3.1 missing-aware dependency scoring policy.

    Each dimension contributes ``weight * value * available`` and the total is
    normalized by the available weight, so a missing timestamp/source metadata
    lowers confidence instead of silently zeroing the whole score.  Decisions
    use three intervals: merge, review_required, independent.
    """

    text_weight: float = 0.30
    entity_weight: float = 0.25
    provenance_weight: float = 0.15
    structure_weight: float = 0.10
    temporal_weight: float = 0.20
    high_threshold: float = 0.55
    low_threshold: float = 0.35
    min_coverage: float = 0.40
    strong_identity_merge: bool = True
    text_review_threshold: float = 0.60

    def __post_init__(self) -> None:
        weights = (
            self.text_weight,
            self.entity_weight,
            self.provenance_weight,
            self.structure_weight,
            self.temporal_weight,
        )
        if abs(sum(weights) - 1.0) > 1e-9:
            raise ValueError("missing-aware weights must sum to 1.0")
        for value in weights:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("weights must be numeric")
            if not 0 <= value <= 1:
                raise ValueError("weights must be between 0 and 1")
        for field_name in (
            "high_threshold",
            "low_threshold",
            "min_coverage",
            "text_review_threshold",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{field_name} must be numeric")
            if not 0 <= value <= 1:
                raise ValueError(f"{field_name} must be between 0 and 1")
        if self.low_threshold > self.high_threshold:
            raise ValueError("low_threshold must not exceed high_threshold")


@dataclass(frozen=True)
class ConstrainedAgglomerationRules:
    """V3.2 feature-based dependency scoring and decision policy.

    Document identity (URL/fingerprint) and continuous lexical similarity are
    separate features; platform identity is a weak provenance feature instead
    of a strong merge signal.  Stage one scores every pair independently and
    stage two executes compatible unions in confidence order, so the final
    decision can explain both ``merge`` and ``review_required`` outcomes.
    """

    document_identity_weight: float = 0.25
    lexical_weight: float = 0.25
    entity_weight: float = 0.20
    provenance_weight: float = 0.10
    template_weight: float = 0.10
    temporal_weight: float = 0.10
    lexical_merge_threshold: float = 0.82
    lexical_review_threshold: float = 0.75
    lexical_independent_threshold: float = 0.75
    score_merge_threshold: float = 0.60
    score_review_threshold: float = 0.45
    min_coverage: float = 0.40
    temporal_window_days: int = 14
    strong_identity_merge: bool = True

    def __post_init__(self) -> None:
        weights = (
            self.document_identity_weight,
            self.lexical_weight,
            self.entity_weight,
            self.provenance_weight,
            self.template_weight,
            self.temporal_weight,
        )
        if abs(sum(weights) - 1.0) > 1e-9:
            raise ValueError("constrained-agglomeration weights must sum to 1.0")
        for value in weights:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("weights must be numeric")
            if not 0 <= value <= 1:
                raise ValueError("weights must be between 0 and 1")
        for field_name in (
            "lexical_merge_threshold",
            "lexical_review_threshold",
            "lexical_independent_threshold",
            "score_merge_threshold",
            "score_review_threshold",
            "min_coverage",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{field_name} must be numeric")
            if not 0 <= value <= 1:
                raise ValueError(f"{field_name} must be between 0 and 1")
        if self.score_review_threshold > self.score_merge_threshold:
            raise ValueError(
                "score_review_threshold must not exceed score_merge_threshold"
            )
        if self.temporal_window_days < 0:
            raise ValueError("temporal_window_days must not be negative")
        if not isinstance(self.strong_identity_merge, bool):
            raise ValueError("strong_identity_merge must be boolean")


@dataclass(frozen=True)
class PairDecision:
    """Full v3.2 pair certificate explaining a cluster formation decision."""

    left_evidence_id: str
    right_evidence_id: str
    raw_decision: str
    final_decision: str
    score: float = 0.0
    confidence: float = 0.0
    coverage: float = 0.0
    union_attempted: bool = False
    union_accepted: bool | None = None
    rejection_reason: str | None = None
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class RobustAggregationRules:
    """Diversity-aware weight aggregation for ``robust-evidence-aggregation.v2``.

    Each independent cluster's weight is capped by how many clusters share the
    same source / enterprise / template group (diminishing return), so a
    repeated group cannot linearly inflate the effective sample size.
    """

    source_cap: float = 0.80
    enterprise_cap: float = 0.80
    template_cap: float = 0.50
    base_weight: float = 1.0
    quality_factor: bool = True
    completeness_factor: bool = True

    def __post_init__(self) -> None:
        for field_name in (
            "source_cap",
            "enterprise_cap",
            "template_cap",
            "base_weight",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{field_name} must be numeric")
            if not 0 < value <= 1:
                raise ValueError(f"{field_name} must be between 0 and 1")


@dataclass(frozen=True)
class MassCappedAggregationRules:
    """Weight-mass capped aggregation for ``robust-evidence-aggregation.v3``.

    ``source_cap = 0.6`` means no single source may own more than 60% of the
    total normalized evidence mass.  Caps are applied iteratively with
    renormalization (source -> enterprise -> template) and N_eff is the
    standard Kish effective sample size over the final probabilities.
    """

    source_cap: float = 0.60
    enterprise_cap: float = 0.60
    template_cap: float = 0.50
    base_weight: float = 1.0
    quality_factor: bool = True
    completeness_factor: bool = True
    max_iterations: int = 500
    tolerance: float = 1e-6

    def __post_init__(self) -> None:
        for field_name in (
            "source_cap",
            "enterprise_cap",
            "template_cap",
            "base_weight",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{field_name} must be numeric")
            if not 0 < value <= 1:
                raise ValueError(f"{field_name} must be between 0 and 1")
        if isinstance(self.max_iterations, bool) or not isinstance(
            self.max_iterations, int
        ):
            raise ValueError("max_iterations must be an integer")
        if self.max_iterations < 1:
            raise ValueError("max_iterations must be positive")
        if (
            isinstance(self.tolerance, bool)
            or not isinstance(self.tolerance, int | float)
            or not 0 < self.tolerance <= 0.1
        ):
            raise ValueError("tolerance must be a small positive number")


@dataclass(frozen=True)
class MassProjectionCertificate:
    """Feasibility certificate for the v4 KL-style mass projection.

    ``converged`` is true only when every source/enterprise/template group
    satisfies its cap within tolerance.  When the constraint set is provably
    infeasible (the group caps of a dimension cannot cover probability mass
    one), ``infeasible`` is true and the violating dimensions are listed.
    """

    converged: bool
    infeasible: bool
    max_constraint_violation: float
    iterations: int
    infeasible_dimensions: tuple[str, ...] = ()
    max_lambda_delta: float = 0.0
    max_probability_delta: float = 0.0
    objective_kl: float = 0.0
    max_complementarity_error: float = 0.0


@dataclass(frozen=True)
class EvidenceAggregationResult:
    """One shared aggregation pipeline result for summary/experiment/LOO."""

    evidence_ids: tuple[str, ...]
    clusters: tuple[tuple[str, ...], ...]
    weights: tuple[tuple[str, float], ...]
    kish_effective_size: float
    entropy_effective_size: float
    projection_certificate: MassProjectionCertificate | None
    raw_distributions: Mapping[str, tuple[DistributionEntry, ...]]
    cluster_distributions: Mapping[str, tuple[DistributionEntry, ...]]
    effective_mass_distributions: Mapping[str, tuple[DistributionEntry, ...]]
    pair_decisions: tuple[tuple[str, str, str], ...] = ()
    pair_certificates: tuple[PairDecision, ...] = ()
    # TEMP-LAG-01: backwards-compatible optional temporal-freshness outputs.
    # Only populated for robust-evidence-aggregation.v5; older versions keep
    # these at their defaults so existing callers are unaffected.
    temporal_certificate: TemporalFreshnessCertificate | None = None
    cluster_freshness: tuple[tuple[str, float, str], ...] = ()
    temporal_algorithm_version: str | None = None
    temporal_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class DistributionEntry:
    group_id: str
    count: int
    share: float


@dataclass(frozen=True)
class EvidenceIndependenceSummary:
    contract_version: str = EVIDENCE_INDEPENDENCE_SUMMARY_VERSION
    subject_ref: str = ""
    release_id: str | None = None
    algorithm_version: str = EVIDENCE_INDEPENDENCE_ALGORITHM_VERSION
    config_hash: str = ""
    coverage_status: CoverageStatus = "unknown"
    raw_evidence_count: int = 0
    evidence_ids: tuple[str, ...] = ()
    independent_cluster_count: int = 0
    effective_sample_size: float = 0.0
    unresolved_ratio: float = 0.0
    source_distribution: tuple[DistributionEntry, ...] = ()
    enterprise_distribution: tuple[DistributionEntry, ...] = ()
    template_distribution: tuple[DistributionEntry, ...] = ()
    uncertainty_state: UncertaintyState = "blocked"
    uncertainty_reasons: tuple[str, ...] = ()
    # v3.1 calibrated abstention decisions. Each tuple is
    # (left_evidence_id, right_evidence_id, decision) with decision in
    # {"merge", "review_required", "independent"}. Older algorithm versions
    # leave these empty.
    pair_decision_count: int = 0
    merge_pair_count: int = 0
    review_required_pair_count: int = 0
    independent_pair_count: int = 0
    review_required_pairs: tuple[tuple[str, str], ...] = ()
    pair_decisions: tuple[tuple[str, str, str], ...] = ()
    # v3.2 full pair certificate; older versions leave this empty.
    pair_certificates: tuple[PairDecision, ...] = ()
    # TEMP-LAG-01: carried from the SAME aggregation that built this summary so
    # InsightCard.temporal_evidence can be filled without a second run.  Legacy
    # (auto/v4) summaries leave these as defaults.
    temporal_certificate: "TemporalFreshnessCertificate | None" = None
    temporal_algorithm_version: str | None = None
    cluster_staleness: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class AblationResult:
    ablation_type: AblationType
    removed_group_id: str | None
    removed_share: float
    removed_count: int
    before_state: UncertaintyState
    after_state: UncertaintyState
    before_effective_sample_size: float
    after_effective_sample_size: float
    before_score: float | None
    after_score: float | None
    before_rank: int | None
    after_rank: int | None
    before_business_state: str
    after_business_state: str
    before_target_found: bool
    after_target_found: bool
    threshold_crossed: bool
    state_changed: bool
    failure_reasons: tuple[str, ...]


@dataclass(frozen=True)
class AblationCertificate:
    contract_version: str = EVIDENCE_INDEPENDENCE_ABLATION_VERSION
    subject_ref: str = ""
    release_id: str | None = None
    algorithm_version: str = EVIDENCE_INDEPENDENCE_ALGORITHM_VERSION
    config_hash: str = ""
    conclusion_provider: str | None = None
    baseline: EvidenceIndependenceSummary | None = None
    ablations: tuple[AblationResult, ...] = ()
    certificate_status: CertificateStatus = "not_applicable"
    certificate_reasons: tuple[str, ...] = ()
