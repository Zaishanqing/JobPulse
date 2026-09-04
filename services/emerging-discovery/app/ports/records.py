"""Persistence records crossing discovery repository ports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from app.domain.discovery import ClusterFeatureSummary, GeneratedDefinition
from app.domain.germination import GerminationAssessmentResult
from app.domain.lineage import LineageRelation
from app.domain.values import FrozenDict, JsonObject


@dataclass(frozen=True)
class RunRecord:
    id: str
    request_id: str
    status: str
    algorithm_version: str
    formula_version: str
    time_window_start: date | None
    time_window_end: date | None
    completed_at: datetime


@dataclass(frozen=True)
class SnapshotRecord:
    id: str
    run_id: str
    source_jd_id: str
    window_id: str
    input_version: str
    schema_version: str
    payload: JsonObject


@dataclass(frozen=True)
class ClusterMembershipRecord:
    id: str
    input_snapshot_id: str
    membership_score: float


@dataclass(frozen=True)
class ClusterAssessmentRecord:
    id: str
    result: GerminationAssessmentResult
    evidence_package: JsonObject
    generated_definition: GeneratedDefinition


@dataclass(frozen=True)
class ClusterAggregate:
    id: str
    run_id: str
    cluster_key: str
    cluster_name: str
    sample_count: int
    core_skills: tuple[str, ...]
    representative_titles: tuple[str, ...]
    representative_members: tuple[JsonObject, ...]
    core_responsibilities: tuple[str, ...]
    semantic_centroid: tuple[float, ...]
    algorithm_sources: tuple[str, ...]
    merge_basis: JsonObject
    stability_score: float
    growth_score: float
    distance_from_existing_positions: float
    feature_summary: ClusterFeatureSummary
    memberships: tuple[ClusterMembershipRecord, ...]
    assessment: ClusterAssessmentRecord


@dataclass(frozen=True)
class LineageRecord:
    id: str
    run_id: str
    relation: LineageRelation


@dataclass(frozen=True)
class AlgorithmConfigRecord:
    id: str
    config: JsonObject


@dataclass(frozen=True)
class CandidateRecord:
    id: str
    status: str
    first_seen_window_id: str
    last_seen_window_id: str
    age: int
    current_cluster_id: str | None
    previous_cluster_ids: tuple[str, ...]
    canonical_title: str
    display_title: str
    definition: JsonObject
    support_count: int
    company_coverage: int
    skill_similarity: float | None
    responsibility_similarity: float | None
    title_similarity: float | None
    membership_overlap: float | None
    identity_similarity: float
    novelty_score: float
    emergence_score: float
    evidence: JsonObject
    identity_stability: int
    titles: tuple[str, ...]
    skills: tuple[str, ...]
    responsibilities: tuple[str, ...]
    member_jd_ids: tuple[str, ...]
    observed_window_ids: tuple[str, ...]
    semantic_centroid: tuple[float, ...]
    created_at: datetime
    updated_at: datetime
    evidence_titles: tuple[str, ...] = ()
    evidence_skills: tuple[str, ...] = ()
    evidence_responsibilities: tuple[str, ...] = ()
    member_evidence_ids: tuple[str, ...] = ()
    member_dedup_cluster_ids: tuple[str, ...] = ()
    member_template_cluster_ids: tuple[str, ...] = ()
    identity_certificate: JsonObject = field(default_factory=FrozenDict)
    lifecycle_state_v2: JsonObject = field(default_factory=FrozenDict)
    identity_resolution_state: str | None = None
    identity_resolution: JsonObject = field(default_factory=FrozenDict)
    canonical_candidate_id: str | None = None


@dataclass(frozen=True)
class CandidateObservationRecord:
    id: str
    candidate_id: str
    run_id: str
    cluster_id: str
    window_id: str
    title: str
    status: str
    emergence_score: float
    support_count: int
    company_count: int
    identity_similarity: float
    skill_similarity: float | None
    responsibility_similarity: float | None
    title_similarity: float | None
    membership_overlap: float | None
    semantic_similarity: float | None = None
    evidence: JsonObject = field(default_factory=FrozenDict)
    match_evidence: JsonObject = field(default_factory=FrozenDict)


@dataclass(frozen=True)
class CandidateLineageReviewRecord:
    review_id: str
    source_window_id: str
    target_window_id: str
    cluster_ids: tuple[str, ...]
    candidate_ids: tuple[str, ...]
    decision_basis: tuple[str, ...]
    hypotheses: tuple[dict[str, object], ...] = ()
    confidence: float | None = None
    algorithm_version: str = ""


@dataclass(frozen=True)
class CandidateIdentityEvidenceSnapshotRecord:
    source_jd_id: str
    source_name: str | None
    company: str | None
    evidence_ref: str


@dataclass(frozen=True)
class AmbiguousIdentityPairRecord:
    observation: CandidateObservationRecord
    candidate_a: CandidateRecord
    candidate_b: CandidateRecord
    candidate_a_snapshots: tuple[CandidateIdentityEvidenceSnapshotRecord, ...]
    candidate_b_snapshots: tuple[CandidateIdentityEvidenceSnapshotRecord, ...]


@dataclass(frozen=True)
class CandidateDiffusionEvidenceRecord:
    input_snapshot_id: str
    source_jd_id: str
    source_fact_id: str
    input_version: str
    window_id: str
    source_name: str | None
    company: str | None
    content_hash: str | None


@dataclass(frozen=True)
class CandidateDiffusionObservationRecord:
    observation: CandidateObservationRecord
    observed_at: datetime
    algorithm_version: str
    formula_version: str
    config_snapshot_id: str
    config_version: str | None
    evidence: tuple[CandidateDiffusionEvidenceRecord, ...]


@dataclass(frozen=True)
class CandidateDiffusionRecord:
    candidate: CandidateRecord
    observations: tuple[CandidateDiffusionObservationRecord, ...]


@dataclass(frozen=True)
class CandidateTransitionRecord:
    id: str
    candidate_id: str
    from_status: str | None
    to_status: str
    reason: str
    run_id: str | None
    window_id: str
    timestamp: datetime
    transition_version: str
    details: JsonObject = field(default_factory=FrozenDict)


@dataclass(frozen=True)
class IdentityResolutionAuditRecord:
    resolution_id: str
    provisional_candidate_id: str
    target_candidate_id: str | None
    decision: str
    reviewer: str
    reason: str
    window_id: str | None
    timestamp: datetime
    algorithm_version: str | None
    idempotency_key: str | None = None
    created_at: datetime | None = None
    details: JsonObject = field(default_factory=FrozenDict)


@dataclass(frozen=True)
class LifecycleWindowRecord:
    window_id: str
    run_id: str
    request_id: str
    algorithm_version: str
    formula_version: str
    completed_at: datetime
    coverage: JsonObject = field(default_factory=FrozenDict)


@dataclass(frozen=True)
class CandidateLifecycleTrajectoryRecord:
    candidate_id: str
    observations: tuple[CandidateObservationRecord, ...]
    transitions: tuple[CandidateTransitionRecord, ...]


@dataclass(frozen=True)
class CandidatePromotionContextRecord:
    candidate: CandidateRecord
    latest_observation: CandidateObservationRecord | None
    window: LifecycleWindowRecord | None
    config_snapshot_id: str | None
    lifecycle_config: JsonObject
