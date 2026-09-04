from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ImmutableMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class DiscoveryRun(ImmutableMixin, Base):
    __tablename__ = "discovery_runs"
    __table_args__ = (
        CheckConstraint("status IN ('succeeded', 'failed')", name="ck_discovery_runs_status"),
        Index("ix_discovery_runs_time_window", "time_window_start", "time_window_end"),
    )
    request_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(64), nullable=False)
    formula_version: Mapped[str] = mapped_column(String(64), nullable=False)
    time_window_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    time_window_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InputSnapshot(ImmutableMixin, Base):
    __tablename__ = "input_snapshots"
    __table_args__ = (
        UniqueConstraint("run_id", "source_jd_id", name="uq_input_snapshots_run_source_jd"),
        Index("ix_input_snapshots_run_window", "run_id", "window_id"),
    )
    run_id: Mapped[str] = mapped_column(ForeignKey("discovery_runs.id"), nullable=False, index=True)
    source_jd_id: Mapped[str] = mapped_column(String(128), nullable=False)
    window_id: Mapped[str] = mapped_column(String(64), nullable=False)
    input_version: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)


class AlgorithmConfigSnapshot(ImmutableMixin, Base):
    __tablename__ = "algorithm_config_snapshots"
    __table_args__ = (UniqueConstraint("run_id", name="uq_algorithm_config_snapshots_run_id"),)
    run_id: Mapped[str] = mapped_column(ForeignKey("discovery_runs.id"), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(64), nullable=False)
    formula_version: Mapped[str] = mapped_column(String(64), nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)


class Cluster(ImmutableMixin, Base):
    __tablename__ = "clusters"
    __table_args__ = (
        UniqueConstraint("run_id", "cluster_key", name="uq_clusters_run_key"),
        CheckConstraint("stability_score BETWEEN 0 AND 1", name="ck_clusters_stability"),
        CheckConstraint("growth_score BETWEEN 0 AND 1", name="ck_clusters_growth"),
        CheckConstraint(
            "distance_from_existing_positions BETWEEN 0 AND 1",
            name="ck_clusters_distance",
        ),
    )
    run_id: Mapped[str] = mapped_column(ForeignKey("discovery_runs.id"), nullable=False, index=True)
    cluster_key: Mapped[str] = mapped_column(String(128), nullable=False)
    cluster_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sample_count: Mapped[int] = mapped_column(nullable=False)
    core_skills: Mapped[list] = mapped_column(JSONB, nullable=False)
    representative_titles: Mapped[list] = mapped_column(JSONB, nullable=False)
    representative_members: Mapped[list] = mapped_column(JSONB, nullable=False)
    core_responsibilities: Mapped[list] = mapped_column(JSONB, nullable=False)
    semantic_centroid: Mapped[list] = mapped_column(JSONB, nullable=False)
    algorithm_sources: Mapped[list] = mapped_column(JSONB, nullable=False)
    merge_basis: Mapped[dict] = mapped_column(JSONB, nullable=False)
    stability_score: Mapped[float] = mapped_column(Float, nullable=False)
    growth_score: Mapped[float] = mapped_column(Float, nullable=False)
    distance_from_existing_positions: Mapped[float] = mapped_column(Float, nullable=False)
    feature_summary: Mapped[dict] = mapped_column(JSONB, nullable=False)


class ClusterMembership(ImmutableMixin, Base):
    __tablename__ = "cluster_memberships"
    __table_args__ = (
        UniqueConstraint(
            "cluster_id",
            "input_snapshot_id",
            name="uq_cluster_memberships_cluster_snapshot",
        ),
        CheckConstraint("membership_score BETWEEN 0 AND 1", name="ck_membership_score"),
    )
    cluster_id: Mapped[str] = mapped_column(ForeignKey("clusters.id"), nullable=False, index=True)
    input_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("input_snapshots.id"), nullable=False, index=True
    )
    membership_score: Mapped[float] = mapped_column(Float, nullable=False)


class ClusterLineage(ImmutableMixin, Base):
    __tablename__ = "cluster_lineages"
    __table_args__ = (
        CheckConstraint(
            "relation_type IN ('birth', 'continue', 'split', 'merge', 'decline', 'absorbed')",
            name="ck_cluster_lineage_relation",
        ),
        CheckConstraint("similarity_score BETWEEN 0 AND 1", name="ck_lineage_score"),
    )
    run_id: Mapped[str] = mapped_column(ForeignKey("discovery_runs.id"), nullable=False, index=True)
    predecessor_cluster_id: Mapped[str | None] = mapped_column(
        ForeignKey("clusters.id"), nullable=True, index=True
    )
    successor_cluster_id: Mapped[str | None] = mapped_column(
        ForeignKey("clusters.id"), nullable=True, index=True
    )
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    similarity_score: Mapped[float] = mapped_column(Float, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    decision_version: Mapped[str] = mapped_column(String(64), nullable=False, default="lineage-v1")


class GerminationAssessment(ImmutableMixin, Base):
    __tablename__ = "germination_assessments"
    __table_args__ = (
        CheckConstraint("score BETWEEN 0 AND 1", name="ck_assessment_score"),
        UniqueConstraint("cluster_id", name="uq_germination_assessments_cluster_id"),
    )
    cluster_id: Mapped[str] = mapped_column(ForeignKey("clusters.id"), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    level: Mapped[str] = mapped_column(String(32), nullable=False)
    qualified_as_emerging: Mapped[bool] = mapped_column(nullable=False)
    dimensions: Mapped[dict] = mapped_column(JSONB, nullable=False)
    evidence_package: Mapped[dict] = mapped_column(JSONB, nullable=False)
    generated_definition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    decision_reason: Mapped[str] = mapped_column(Text, nullable=False)


class Candidate(Base):
    __tablename__ = "candidates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('weak_signal', 'incubating', 'emerging_candidate', "
            "'stable_emerging_role', 'official_position', 'dead', 'noise')",
            name="ck_candidates_status",
        ),
        CheckConstraint("age >= 0", name="ck_candidates_age"),
        CheckConstraint("support_count >= 0", name="ck_candidates_support"),
        CheckConstraint("company_coverage >= 0", name="ck_candidates_companies"),
        CheckConstraint("identity_similarity BETWEEN 0 AND 1", name="ck_candidates_identity"),
        CheckConstraint("novelty_score BETWEEN 0 AND 1", name="ck_candidates_novelty"),
        CheckConstraint("emergence_score BETWEEN 0 AND 1", name="ck_candidates_emergence"),
        Index("ix_candidates_status", "status"),
        Index("ix_candidates_last_seen_window", "last_seen_window_id"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    first_seen_window_id: Mapped[str] = mapped_column(String(64), nullable=False)
    last_seen_window_id: Mapped[str] = mapped_column(String(64), nullable=False)
    age: Mapped[int] = mapped_column(Integer, nullable=False)
    current_cluster_id: Mapped[str | None] = mapped_column(
        ForeignKey("clusters.id"), nullable=True, index=True
    )
    previous_cluster_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    canonical_title: Mapped[str] = mapped_column(String(255), nullable=False)
    display_title: Mapped[str] = mapped_column(String(255), nullable=False)
    definition: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    support_count: Mapped[int] = mapped_column(Integer, nullable=False)
    company_coverage: Mapped[int] = mapped_column(Integer, nullable=False)
    skill_similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    responsibility_similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    title_similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    membership_overlap: Mapped[float | None] = mapped_column(Float, nullable=True)
    identity_similarity: Mapped[float] = mapped_column(Float, nullable=False)
    novelty_score: Mapped[float] = mapped_column(Float, nullable=False)
    emergence_score: Mapped[float] = mapped_column(Float, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    identity_stability: Mapped[int] = mapped_column(Integer, nullable=False)
    identity_profile: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class CandidateClusterObservation(ImmutableMixin, Base):
    __tablename__ = "candidate_cluster_observations"
    __table_args__ = (
        UniqueConstraint("candidate_id", "cluster_id", name="uq_candidate_observation_cluster"),
        UniqueConstraint(
            "candidate_id",
            "window_id",
            name="uq_candidate_observation_window",
        ),
        CheckConstraint(
            "status IN ('weak_signal', 'incubating', 'emerging_candidate', "
            "'stable_emerging_role', 'official_position', 'dead', 'noise')",
            name="ck_candidate_observation_status",
        ),
        CheckConstraint("emergence_score BETWEEN 0 AND 1", name="ck_candidate_obs_emergence"),
        CheckConstraint("identity_similarity BETWEEN 0 AND 1", name="ck_candidate_obs_identity"),
        CheckConstraint("support_count >= 0", name="ck_candidate_obs_support"),
        CheckConstraint("company_count >= 0", name="ck_candidate_obs_companies"),
        Index("ix_candidate_observations_window", "candidate_id", "window_id"),
    )
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidates.id"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("discovery_runs.id"), nullable=False, index=True
    )
    cluster_id: Mapped[str] = mapped_column(ForeignKey("clusters.id"), nullable=False, index=True)
    window_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    emergence_score: Mapped[float] = mapped_column(Float, nullable=False)
    support_count: Mapped[int] = mapped_column(Integer, nullable=False)
    company_count: Mapped[int] = mapped_column(Integer, nullable=False)
    identity_similarity: Mapped[float] = mapped_column(Float, nullable=False)
    skill_similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    responsibility_similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    title_similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    membership_overlap: Mapped[float | None] = mapped_column(Float, nullable=True)
    semantic_similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    match_evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class CandidateLineageRelation(ImmutableMixin, Base):
    __tablename__ = "candidate_lineage_relations"
    __table_args__ = (
        CheckConstraint(
            "relation_type IN ('CONTINUE', 'SPLIT', 'MERGE')",
            name="ck_candidate_lineage_relation_type",
        ),
        CheckConstraint(
            "confidence BETWEEN 0 AND 1",
            name="ck_candidate_lineage_confidence",
        ),
        CheckConstraint(
            "support_inflation = 0",
            name="ck_candidate_lineage_support_inflation",
        ),
        CheckConstraint(
            "observation_delta = 0",
            name="ck_candidate_lineage_observation_delta",
        ),
        UniqueConstraint(
            "relation_id",
            name="uq_candidate_lineage_relation_id",
        ),
        Index(
            "ix_candidate_lineage_transition",
            "source_window_id",
            "target_window_id",
        ),
    )
    relation_id: Mapped[str] = mapped_column(String(160), nullable=False)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("discovery_runs.id"), nullable=False, index=True
    )
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_candidate_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    target_candidate_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    source_window_id: Mapped[str] = mapped_column(String(64), nullable=False)
    target_window_id: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence: Mapped[list] = mapped_column(JSONB, nullable=False)
    decision_basis: Mapped[list] = mapped_column(JSONB, nullable=False)
    review_required: Mapped[bool] = mapped_column(nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(128), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_cluster_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    target_cluster_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    proposed_target_candidate_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list
    )
    support_inflation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    observation_delta: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CandidateLineageReview(ImmutableMixin, Base):
    __tablename__ = "candidate_lineage_reviews"
    __table_args__ = (
        CheckConstraint(
            "confidence IS NULL OR (confidence BETWEEN 0 AND 1)",
            name="ck_candidate_lineage_review_confidence",
        ),
        UniqueConstraint(
            "review_id",
            name="uq_candidate_lineage_review_id",
        ),
        Index(
            "ix_candidate_lineage_review_transition",
            "source_window_id",
            "target_window_id",
        ),
    )
    review_id: Mapped[str] = mapped_column(String(160), nullable=False)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("discovery_runs.id"), nullable=False, index=True
    )
    source_window_id: Mapped[str] = mapped_column(String(64), nullable=False)
    target_window_id: Mapped[str] = mapped_column(String(64), nullable=False)
    cluster_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    candidate_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    decision_basis: Mapped[list] = mapped_column(JSONB, nullable=False)
    hypotheses: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    algorithm_version: Mapped[str] = mapped_column(String(128), nullable=False)


class CandidateStatusTransition(ImmutableMixin, Base):
    __tablename__ = "candidate_status_transitions"
    __table_args__ = (
        CheckConstraint(
            "from_status IS NULL OR from_status IN "
            "('weak_signal', 'incubating', 'emerging_candidate', "
            "'stable_emerging_role', 'official_position', 'dead', 'noise')",
            name="ck_candidate_transition_from",
        ),
        CheckConstraint(
            "to_status IN ('weak_signal', 'incubating', 'emerging_candidate', "
            "'stable_emerging_role', 'official_position', 'dead', 'noise')",
            name="ck_candidate_transition_to",
        ),
        Index("ix_candidate_transitions_candidate", "candidate_id"),
        Index("ix_candidate_transitions_window", "candidate_id", "window_id"),
    )
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidates.id"), nullable=False, index=True
    )
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("discovery_runs.id"), nullable=True, index=True
    )
    window_id: Mapped[str] = mapped_column(String(64), nullable=False)
    transition_version: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IdentityResolutionAudit(ImmutableMixin, Base):
    __tablename__ = "identity_resolution_audits"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('confirm_same', 'confirm_new')",
            name="ck_identity_resolution_decision",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_identity_resolution_idempotency_key",
        ),
        Index(
            "ix_identity_resolution_provisional_candidate",
            "provisional_candidate_id",
        ),
    )
    provisional_candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidates.id"), nullable=False
    )
    target_candidate_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reviewer: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    window_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    algorithm_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class DiscoveryMaintenanceAudit(Base):
    __tablename__ = "discovery_maintenance_audits"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def _reject_mutation(_mapper, _connection, target) -> None:
    raise ValueError(f"{type(target).__name__} records are immutable")


for _model in (
    DiscoveryRun,
    InputSnapshot,
    AlgorithmConfigSnapshot,
    Cluster,
    ClusterMembership,
    ClusterLineage,
    GerminationAssessment,
    CandidateLineageRelation,
    CandidateLineageReview,
    CandidateClusterObservation,
    CandidateStatusTransition,
):
    event.listen(_model, "before_update", _reject_mutation)
    event.listen(_model, "before_delete", _reject_mutation)
