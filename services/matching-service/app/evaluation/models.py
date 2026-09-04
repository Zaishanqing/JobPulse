"""Immutable contracts for anonymous, reproducible offline evaluation."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.domain.profiles import CVMatchProfile, ImmutableDTO, PositionMatchProfile
from app.domain.skill_relations import SkillRelation

EvaluationLabel = Literal["matched", "partial", "not_matched", "unknown"]
EvaluationDimension = Literal[
    "hard_constraint",
    "required_skill",
    "bonus_skill",
    "responsibility",
    "project",
    "scenario",
]
AblationMode = Literal[
    "deterministic_only",
    "deterministic_plus_graph",
    "deterministic_plus_semantic",
    "full_fusion",
]
RecommendationLabel = Literal[
    "strong_match",
    "potential_match",
    "weak_match",
    "not_recommended",
    "insufficient_information",
]
HardGateLabel = Literal["passed", "failed", "uncertain", "not_applicable"]
SkillRelationLabel = Literal["exact", "equivalent", "related", "transferable", "unknown"]


class LabeledEmbedding(ImmutableDTO):
    vector_text: str = Field(min_length=1)
    embedding: tuple[float, ...] = Field(min_length=1)


class RequirementAnnotation(ImmutableDTO):
    requirement_id: str = Field(min_length=1)
    dimension: EvaluationDimension
    label: EvaluationLabel
    candidate_feature_id: str | None = None
    semantic_score: float | None = Field(default=None, ge=-1, le=1)
    relevant_rank: int | None = Field(default=None, ge=1)
    dense_rank: int | None = Field(default=None, ge=1)
    sparse_rank: int | None = Field(default=None, ge=1)
    dense_score: float | None = Field(default=None, ge=-1, le=1)
    sparse_score: float | None = Field(default=None, ge=0, le=1)
    rerank_score: float | None = Field(default=None, ge=-1, le=1)


class ExpectedSkillRelation(ImmutableDTO):
    requirement_id: str = Field(min_length=1)
    relation_type: SkillRelationLabel
    evidence_required: bool = True


class LearningPrerequisiteExpectation(ImmutableDTO):
    target_skill_id: str = Field(min_length=1)
    prerequisite_skill_id: str = Field(min_length=1)


StageEStrategy = Literal[
    "rule",
    "dense",
    "rule_dense",
    "hybrid",
    "hybrid_reranker",
    "rule_hybrid_graph",
]


class StageEPolicy(ImmutableDTO):
    dense_weight: float = Field(ge=0, le=1)
    sparse_weight: float = Field(ge=0, le=1)
    top_k: int = Field(ge=1, le=100)
    threshold: float = Field(ge=0, le=1)
    rrf_k: int = Field(ge=1)
    reranker_top_n: Literal[10, 20]
    reranker_model_revision: str = Field(min_length=1)


class StageEStrategyReport(ImmutableDTO):
    strategy: StageEStrategy
    metrics: BinaryMetrics
    hard_gate_accuracy: float | None = Field(default=None, ge=0, le=1)


class EvaluationSampleReference(ImmutableDTO):
    sample_id: str = Field(min_length=1)
    cv_profile_id: str = Field(min_length=1)
    position_profile_id: str = Field(min_length=1)


class OfflineEvaluationReportV2(ImmutableDTO):
    report_version: Literal["offline-evaluation-report.v2"] = "offline-evaluation-report.v2"
    offline_algorithm_version: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    annotation_version: str = Field(min_length=1)
    policy: StageEPolicy
    strategies: tuple[StageEStrategyReport, ...] = Field(min_length=1)
    sample_references: tuple[EvaluationSampleReference, ...]
    result_id: str = Field(min_length=1)
    fixture_notice: Literal["anonymous_fixture_not_business_accuracy"]


class OfflineSample(ImmutableDTO):
    sample_id: str = Field(min_length=1)
    cv_profile: CVMatchProfile
    position_profile: PositionMatchProfile
    annotations: tuple[RequirementAnnotation, ...] = Field(min_length=1)
    expected_recommendation: RecommendationLabel
    expected_hard_gate_status: HardGateLabel
    skill_relations: tuple[SkillRelation, ...] = ()
    embedding_vectors: tuple[LabeledEmbedding, ...] = ()
    expected_skill_relations: tuple[ExpectedSkillRelation, ...] = ()
    expected_learning_prerequisites: tuple[LearningPrerequisiteExpectation, ...] = ()
    expected_semantic_no_score_requirement_ids: tuple[str, ...] = ()
    time_budget_hours: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def unique_annotation_and_vector_keys(self) -> OfflineSample:
        requirement_ids = [item.requirement_id for item in self.annotations]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("annotation requirement_id values must be unique per sample")
        vector_texts = [item.vector_text for item in self.embedding_vectors]
        if len(vector_texts) != len(set(vector_texts)):
            raise ValueError("embedding vector_text values must be unique per sample")
        return self


class OfflineDataset(ImmutableDTO):
    dataset_version: str = Field(min_length=1)
    annotation_version: str = Field(min_length=1)
    samples: tuple[OfflineSample, ...] = Field(min_length=1)
    fixture_notice: Literal["anonymous_fixture_not_business_accuracy"] = (
        "anonymous_fixture_not_business_accuracy"
    )

    @model_validator(mode="after")
    def unique_sample_ids(self) -> OfflineDataset:
        sample_ids = [item.sample_id for item in self.samples]
        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError("offline sample_id values must be unique")
        return self


class BinaryMetrics(ImmutableDTO):
    precision: float | None = Field(default=None, ge=0, le=1)
    recall: float | None = Field(default=None, ge=0, le=1)
    f1: float | None = Field(default=None, ge=0, le=1)
    true_positive: int = Field(ge=0)
    false_positive: int = Field(ge=0)
    false_negative: int = Field(ge=0)
    support: int = Field(ge=0)


class ConfusionCell(ImmutableDTO):
    actual: EvaluationLabel
    predicted: EvaluationLabel
    count: int = Field(ge=0)


class DimensionMetrics(ImmutableDTO):
    dimension: EvaluationDimension
    metrics: BinaryMetrics
    support: int = Field(ge=0)


class TopKRecall(ImmutableDTO):
    k: int = Field(ge=1)
    recall: float | None = Field(default=None, ge=0, le=1)
    eligible_count: int = Field(ge=0)


class UncertaintyCoverage(ImmutableDTO):
    total_results: int = Field(ge=0)
    unknown_count: int = Field(ge=0)
    unresolved_count: int = Field(ge=0)
    unknown_rate: float = Field(ge=0, le=1)
    unresolved_rate: float = Field(ge=0, le=1)


class AblationReport(ImmutableDTO):
    mode: AblationMode
    overall_metrics: BinaryMetrics
    confusion_matrix: tuple[ConfusionCell, ...]
    top_k_recall: tuple[TopKRecall, ...]
    mean_reciprocal_rank: float | None = Field(default=None, ge=0, le=1)
    dimension_metrics: tuple[DimensionMetrics, ...]
    uncertainty_coverage: UncertaintyCoverage
    recommendation_accuracy: float | None = Field(default=None, ge=0, le=1)
    hard_gate_accuracy: float | None = Field(default=None, ge=0, le=1)
    completed_samples: int = Field(ge=0)
    rejected_samples: int = Field(ge=0)


class ThresholdCandidateReport(ImmutableDTO):
    threshold: float = Field(ge=0, le=1)
    metrics: BinaryMetrics


class ThresholdCalibrationReport(ImmutableDTO):
    selection_rule: Literal["max_f1_then_precision_recall_then_higher_threshold"] = (
        "max_f1_then_precision_recall_then_higher_threshold"
    )
    candidates: tuple[ThresholdCandidateReport, ...]
    recommended_threshold: float | None = Field(default=None, ge=0, le=1)
    production_config_changed: Literal[False] = False


class EvaluationVersions(ImmutableDTO):
    offline_algorithm_version: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    annotation_version: str = Field(min_length=1)
    matching_algorithm_version: str = Field(min_length=1)
    scoring_algorithm_version: str = Field(min_length=1)
    scoring_config_version: str = Field(min_length=1)
    semantic_algorithm_version: str = Field(min_length=1)
    semantic_threshold_config_version: str = Field(min_length=1)
    vector_text_derivation_version: str = Field(min_length=1)
    embedding_model: str = Field(min_length=1)
    embedding_version: str = Field(min_length=1)


class OfflineEvaluationReport(ImmutableDTO):
    report_version: Literal["offline-evaluation-report.v1"] = (
        "offline-evaluation-report.v1"
    )
    versions: EvaluationVersions
    sample_references: tuple[EvaluationSampleReference, ...]
    ablations: tuple[AblationReport, ...]
    threshold_calibration: ThresholdCalibrationReport
    run_started_at: datetime
    duration_ms: float = Field(ge=0)
    result_id: str = Field(min_length=1)
    fixture_notice: Literal["anonymous_fixture_not_business_accuracy"] = (
        "anonymous_fixture_not_business_accuracy"
    )


class CompetitionMetrics(ImmutableDTO):
    mean_reciprocal_rank: float | None = Field(default=None, ge=0, le=1)
    hard_gate_accuracy: float | None = Field(default=None, ge=0, le=1)
    relation_explanation_accuracy: float | None = Field(default=None, ge=0, le=1)
    semantic_no_score_accuracy: float | None = Field(default=None, ge=0, le=1)
    learning_path_order_accuracy: float | None = Field(default=None, ge=0, le=1)
    sample_count: int = Field(ge=0)


class CompetitionSampleResult(ImmutableDTO):
    sample_id: str = Field(min_length=1)
    mean_reciprocal_rank: float | None = Field(default=None, ge=0, le=1)
    hard_gate_accuracy: float = Field(ge=0, le=1)
    relation_explanation_accuracy: float = Field(ge=0, le=1)
    semantic_no_score_accuracy: float = Field(ge=0, le=1)
    learning_path_order_accuracy: float = Field(ge=0, le=1)


class CompetitionEvaluationReport(ImmutableDTO):
    report_version: Literal["competition-evaluation-report.v1"] = (
        "competition-evaluation-report.v1"
    )
    dataset_version: str = Field(min_length=1)
    annotation_version: str = Field(min_length=1)
    metrics: CompetitionMetrics
    samples: tuple[CompetitionSampleResult, ...]
    result_id: str = Field(min_length=1)
    fixture_notice: Literal["anonymous_fixture_not_business_accuracy"] = (
        "anonymous_fixture_not_business_accuracy"
    )
