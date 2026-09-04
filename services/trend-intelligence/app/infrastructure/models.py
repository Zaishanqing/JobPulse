from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AnalysisRunModel(Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','succeeded','failed','cancelled')",
            name="ck_analysis_runs_status",
        ),
        Index("ix_analysis_runs_claim", "status", "available_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    contract_version: Mapped[str] = mapped_column(String(32), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data_sources: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    weights: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(128), nullable=False)
    formula_version: Mapped[str] = mapped_column(String(128), nullable=False)
    run_type: Mapped[str] = mapped_column(String(32), nullable=False, default="market_prediction", index=True)
    run_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    position_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    graph_version: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    config_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(nullable=False, default=3)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AnalysisRunLogModel(Base):
    __tablename__ = "analysis_run_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class SourceSnapshotModel(Base):
    __tablename__ = "source_snapshots"
    __table_args__ = (
        UniqueConstraint("source", "external_id", "source_version", name="uq_source_snapshot_identity"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    first_seen_run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(256), nullable=False)
    source_version: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    date_precision: Mapped[str] = mapped_column(String(16), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    content_completeness: Mapped[str] = mapped_column(String(16), nullable=False)
    event_cluster_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    snapshot_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class TrendInputRecordModel(Base):
    __tablename__ = "trend_input_records"
    __table_args__ = (
        UniqueConstraint(
            "bundle_id", "acquisition_snapshot_id", name="uq_trend_input_bundle_snapshot",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    bundle_id: Mapped[str] = mapped_column(
        ForeignKey("acquisition_bundles.id"), nullable=False, index=True,
    )
    acquisition_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("raw_snapshots.id"), nullable=False, index=True,
    )
    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.id"), nullable=False, index=True,
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(256), nullable=False)
    source_version: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    record_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class EvidenceModel(Base):
    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint("analysis_run_id", "snapshot_id", name="uq_evidence_run_snapshot"),
        Index("ix_evidence_run_cluster", "analysis_run_id", "event_cluster_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_snapshots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_cluster_id: Mapped[str] = mapped_column(String(36), nullable=False)
    contribution_weight: Mapped[float] = mapped_column(Float, nullable=False)
    quality_flags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class ExtractedTermModel(Base):
    __tablename__ = "extracted_terms"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "term", "extractor_version", name="uq_extracted_term_identity"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("source_snapshots.id"), nullable=False, index=True)
    term: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    week_start: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    extractor_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class SignalObservationModel(Base):
    __tablename__ = "signal_observations"
    __table_args__ = (
        UniqueConstraint("analysis_run_id", "source", "industry_domain", "week_start", name="uq_signal_run_source_domain_week"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    analysis_run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    industry_domain: Mapped[str] = mapped_column(String(128), nullable=False)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    signal_strength: Mapped[float] = mapped_column(Float, nullable=False)
    raw_value: Mapped[float] = mapped_column(Float, nullable=False)
    keywords: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    evidence_snapshot_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class PredictionResultModel(Base):
    __tablename__ = "prediction_results"
    __table_args__ = (
        UniqueConstraint("analysis_run_id", "job_name", "industry_domain", name="uq_prediction_run_job_domain"),
        CheckConstraint("emergence_score BETWEEN 0 AND 1", name="ck_prediction_emergence_score"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    analysis_run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False, index=True)
    job_name: Mapped[str] = mapped_column(String(256), nullable=False)
    industry_domain: Mapped[str] = mapped_column(String(256), nullable=False)
    emergence_score: Mapped[float] = mapped_column(Float, nullable=False)
    source_scores: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False)
    related_keywords: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    evidence_snapshot_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(128), nullable=False)
    formula_version: Mapped[str] = mapped_column(String(128), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_coverage: Mapped[float] = mapped_column(Float, nullable=False)
    missing_sources: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    quality_flags: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    config_versions: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    score_explanation: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class RunSourceStatusModel(Base):
    __tablename__ = "run_source_status"
    __table_args__ = (
        UniqueConstraint("analysis_run_id", "source", name="uq_run_source_status"),
        CheckConstraint("status IN ('pending','running','succeeded','failed')", name="ck_run_source_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    analysis_run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    records_fetched: Mapped[int] = mapped_column(nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class PositionSkillTrendResultModel(Base):
    __tablename__ = "position_skill_trend_results"
    __table_args__ = (
        UniqueConstraint("analysis_run_id", "position_id", "graph_version", name="uq_skill_trend_run_position_graph"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    analysis_run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False, index=True)
    position_id: Mapped[str] = mapped_column(String(128), nullable=False)
    position_name: Mapped[str] = mapped_column(String(255), nullable=False)
    graph_version: Mapped[str] = mapped_column(String(128), nullable=False)
    skill_catalog_version: Mapped[str] = mapped_column(String(128), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(128), nullable=False)
    formula_version: Mapped[str] = mapped_column(String(128), nullable=False)
    config_version: Mapped[str] = mapped_column(String(128), nullable=False)
    result_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class TrendChangeAnalysisModel(Base):
    __tablename__ = "trend_change_analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    algorithm_version: Mapped[str] = mapped_column(String(128), nullable=False)
    config_version: Mapped[str] = mapped_column(String(128), nullable=False)
    result_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class AlgorithmConfigurationModel(Base):
    __tablename__ = "algorithm_configurations"
    __table_args__ = (
        UniqueConstraint("config_type", "version", name="uq_algorithm_config_type_version"),
        CheckConstraint("status IN ('draft','active','inactive')", name="ck_algorithm_config_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    config_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", index=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AlgorithmConfigurationEventModel(Base):
    __tablename__ = "algorithm_configuration_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    configuration_id: Mapped[str] = mapped_column(
        ForeignKey("algorithm_configurations.id"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class BacktestRunModel(Base):
    __tablename__ = "backtest_runs"
    __table_args__ = (
        UniqueConstraint("request_id", name="uq_backtest_request_id"),
        UniqueConstraint("idempotency_key", name="uq_backtest_idempotency_key"),
        CheckConstraint("status IN ('pending','running','succeeded','failed')", name="ck_backtest_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    config_versions: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    dataset_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    dataset_version: Mapped[str] = mapped_column(String(128), nullable=False)
    request_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BacktestSliceResultModel(Base):
    __tablename__ = "backtest_slice_results"
    __table_args__ = (
        UniqueConstraint("backtest_run_id", "slice_key", name="uq_backtest_slice_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    backtest_run_id: Mapped[str] = mapped_column(ForeignKey("backtest_runs.id"), nullable=False, index=True)
    slice_key: Mapped[str] = mapped_column(String(128), nullable=False)
    observation_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    validation_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    predictions: Mapped[list] = mapped_column(JSON, nullable=False)
    ground_truth: Mapped[list] = mapped_column(JSON, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False)
    ablation_results: Mapped[dict] = mapped_column(JSON, nullable=False)
    stability_results: Mapped[dict] = mapped_column(JSON, nullable=False)
    quality_flags: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class EvaluationDatasetModel(Base):
    __tablename__ = "evaluation_datasets"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_evaluation_dataset_name_version"),
        CheckConstraint(
            "status IN ('draft','labeling','review','published','superseded')",
            name="ck_evaluation_dataset_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", index=True)
    parent_dataset_id: Mapped[str | None] = mapped_column(ForeignKey("evaluation_datasets.id"), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    published_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvaluationSampleModel(Base):
    __tablename__ = "evaluation_samples"
    __table_args__ = (
        UniqueConstraint("dataset_id", "sample_key", name="uq_evaluation_sample_dataset_key"),
        CheckConstraint(
            "status IN ('pending','annotated','conflict','approved')",
            name="ck_evaluation_sample_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    dataset_id: Mapped[str] = mapped_column(ForeignKey("evaluation_datasets.id"), nullable=False, index=True)
    sample_key: Mapped[str] = mapped_column(String(256), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_name: Mapped[str] = mapped_column(String(255), nullable=False)
    prediction_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    label_window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    label_window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(512), nullable=False)
    source_dedup_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    evidence: Mapped[list] = mapped_column(JSON, nullable=False)
    quality_flags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class EvaluationLabelModel(Base):
    __tablename__ = "evaluation_labels"
    __table_args__ = (
        UniqueConstraint("sample_id", "version", name="uq_evaluation_label_sample_version"),
        CheckConstraint(
            "status IN ('submitted','approved','rejected','conflict')",
            name="ck_evaluation_label_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    sample_id: Mapped[str] = mapped_column(ForeignKey("evaluation_samples.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(nullable=False)
    label_type: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence: Mapped[list] = mapped_column(JSON, nullable=False)
    confidence_level: Mapped[str] = mapped_column(String(16), nullable=False)
    annotator_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reviewer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="submitted")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvaluationDatasetEventModel(Base):
    __tablename__ = "evaluation_dataset_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    dataset_id: Mapped[str] = mapped_column(ForeignKey("evaluation_datasets.id"), nullable=False, index=True)
    sample_id: Mapped[str | None] = mapped_column(ForeignKey("evaluation_samples.id"), nullable=True)
    label_id: Mapped[str | None] = mapped_column(ForeignKey("evaluation_labels.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class SourceFetchAttemptModel(Base):
    __tablename__ = "source_fetch_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    analysis_run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False)
    records_count: Mapped[int] = mapped_column(nullable=False)
    duplicate_count: Mapped[int] = mapped_column(nullable=False)
    field_completeness: Mapped[float] = mapped_column(Float, nullable=False)
    freshness_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cache_snapshot_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class ReplayCacheModel(Base):
    __tablename__ = "source_replay_cache"
    __table_args__ = (
        UniqueConstraint("analysis_run_id", "source", name="uq_replay_cache_run_source"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    analysis_run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    records_payload: Mapped[list] = mapped_column(JSON, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class SourceCircuitStateModel(Base):
    __tablename__ = "source_circuit_states"

    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="closed")
    consecutive_failures: Mapped[int] = mapped_column(nullable=False, default=0)
    opened_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
