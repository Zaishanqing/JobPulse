"""Reproducible offline ablation runner over anonymous labeled fixtures."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.application.evaluation import MatchEvaluationService
from app.domain.evaluation import MatchEvaluation
from uuid import uuid4
from app.domain.matching import MatchingAlgorithmConfig
from app.domain.privacy import find_pii
from app.domain.profiles import CVMatchProfile, PositionMatchProfile
from app.domain.scoring import ScoringConfig
from app.domain.skill_relations import SkillRelation
from app.evaluation.metrics import (
    binary_metrics,
    calibrate_thresholds,
    confusion_matrix,
    dimension_metrics,
    mean_reciprocal_rank,
    top_k_recall,
    uncertainty_coverage,
)
from app.evaluation.models import (
    AblationMode,
    AblationReport,
    EvaluationDimension,
    EvaluationLabel,
    EvaluationVersions,
    HardGateLabel,
    EvaluationSampleReference,
    LabeledEmbedding,
    OfflineDataset,
    OfflineEvaluationReport,
    OfflineSample,
    RecommendationLabel,
    RequirementAnnotation,
)
from app.infrastructure.relation_sources import InMemorySkillRelationSource

_MODES: tuple[AblationMode, ...] = (
    "deterministic_only",
    "deterministic_plus_graph",
    "deterministic_plus_semantic",
    "full_fusion",
)


class _ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _SampleManifest(_ManifestModel):
    sample_id: str = Field(min_length=1)
    cv_profile_path: str = Field(min_length=1)
    position_profile_path: str = Field(min_length=1)
    annotations: tuple[RequirementAnnotation, ...] = Field(min_length=1)
    expected_recommendation: RecommendationLabel
    expected_hard_gate_status: HardGateLabel
    skill_relations_path: str | None = None
    embedding_vectors: tuple[LabeledEmbedding, ...] = ()


class _DatasetManifest(_ManifestModel):
    dataset_version: str = Field(min_length=1)
    annotation_version: str = Field(min_length=1)
    samples: tuple[_SampleManifest, ...] = Field(min_length=1)
    fixture_notice: Literal["anonymous_fixture_not_business_accuracy"]


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def load_dataset(path: str | Path) -> OfflineDataset:
    """Resolve a versioned manifest into strict Domain profile inputs."""
    manifest_path = Path(path).resolve()
    manifest = _DatasetManifest.model_validate(_read_json(manifest_path))
    samples = []
    for item in manifest.samples:
        cv_payload = _read_json((manifest_path.parent / item.cv_profile_path).resolve())
        position_payload = _read_json(
            (manifest_path.parent / item.position_profile_path).resolve()
        )
        if find_pii(cv_payload) or find_pii(position_payload):
            raise ValueError(f"offline sample {item.sample_id} contains PII")
        relations: tuple[SkillRelation, ...] = ()
        if item.skill_relations_path:
            relation_payload = _read_json(
                (manifest_path.parent / item.skill_relations_path).resolve()
            )
            values = (
                relation_payload.get("relations", [])
                if isinstance(relation_payload, dict)
                else relation_payload
            )
            if not isinstance(values, list):
                raise ValueError("skill relation fixture must contain a relation list")
            relations = tuple(SkillRelation.model_validate(value) for value in values)
        samples.append(
            OfflineSample(
                sample_id=item.sample_id,
                cv_profile=CVMatchProfile.model_validate(cv_payload),
                position_profile=PositionMatchProfile.model_validate(position_payload),
                annotations=item.annotations,
                expected_recommendation=item.expected_recommendation,
                expected_hard_gate_status=item.expected_hard_gate_status,
                skill_relations=relations,
                embedding_vectors=item.embedding_vectors,
            )
        )
    return OfflineDataset(
        dataset_version=manifest.dataset_version,
        annotation_version=manifest.annotation_version,
        samples=tuple(samples),
        fixture_notice=manifest.fixture_notice,
    )


def _prediction_label(status: str) -> EvaluationLabel:
    if status in {"matched", "pass"}:
        return "matched"
    if status in {"partial", "weak", "declared_only"}:
        return "partial"
    if status in {"missing", "not_observed", "fail"}:
        return "not_matched"
    return "unknown"


def _predictions(
    evaluation: MatchEvaluation,
) -> dict[str, tuple[EvaluationDimension, EvaluationLabel]]:
    values: dict[str, tuple[EvaluationDimension, EvaluationLabel]] = {}
    for item in evaluation.hard_constraint_results:
        values[item.requirement_id] = (
            "hard_constraint",
            _prediction_label(item.status),
        )
    for item in evaluation.skill_results:
        dimension: EvaluationDimension = (
            "required_skill" if item.importance_level == "required" else "bonus_skill"
        )
        values[item.requirement_id] = (dimension, _prediction_label(item.match_status))
    for dimension, results in (
        ("responsibility", evaluation.responsibility_results),
        ("project", evaluation.project_results),
        ("scenario", evaluation.scenario_results),
    ):
        for item in results:
            values[item.requirement_id] = (dimension, _prediction_label(item.match_status))
    return values


class OfflineEvaluator:
    def __init__(
        self,
        *,
        matching_config: MatchingAlgorithmConfig | None = None,
        scoring_config: ScoringConfig | None = None,
        embedding_model: str = "offline-fixture-embedding",
        embedding_version: str = "offline-fixture-embedding.v1",
        algorithm_version: str = "offline-evaluation.v1",
    ) -> None:
        self._matching = matching_config or MatchingAlgorithmConfig()
        self._scoring = scoring_config or ScoringConfig()
        self._embedding_model = embedding_model
        self._embedding_version = embedding_version
        self._algorithm_version = algorithm_version

    def run(
        self,
        dataset: OfflineDataset,
        *,
        threshold_candidates: tuple[float, ...] = (0.7, 0.8, 0.82, 0.85, 0.9),
        run_started_at: datetime | None = None,
    ) -> OfflineEvaluationReport:
        started_clock = time.perf_counter()
        started_at = run_started_at or datetime.now(timezone.utc)
        source_annotations = tuple(
            annotation for sample in dataset.samples for annotation in sample.annotations
        )
        annotations = source_annotations
        ablations = tuple(
            self._run_ablation(dataset, mode, annotations) for mode in _MODES
        )
        calibration = calibrate_thresholds(annotations, threshold_candidates)
        inputs = tuple(
            EvaluationSampleReference(
                sample_id=sample.sample_id,
                cv_profile_id=sample.cv_profile.profile_version or "",
                position_profile_id=(
                    sample.position_profile.profile_version or ""
                ),
            )
            for sample in dataset.samples
        )
        versions = EvaluationVersions(
            offline_algorithm_version=self._algorithm_version,
            dataset_version=dataset.dataset_version,
            annotation_version=dataset.annotation_version,
            matching_algorithm_version=self._matching.algorithm_version,
            scoring_algorithm_version=self._scoring.algorithm_version,
            scoring_config_version=self._scoring.scoring_config_version,
            semantic_algorithm_version="semantic-disabled",
            semantic_threshold_config_version="semantic-disabled",
            vector_text_derivation_version="semantic-fragment.v1",
            embedding_model=self._embedding_model,
            embedding_version=self._embedding_version,
        )
        report_metadata = {
            "report_version": "offline-evaluation-report.v1",
            "versions": versions.model_dump(mode="json"),
            "sample_references": [item.model_dump(mode="json") for item in inputs],
            "ablations": [item.model_dump(mode="json") for item in ablations],
            "threshold_calibration": calibration.model_dump(mode="json"),
            "fixture_notice": dataset.fixture_notice,
        }
        result_id = "report_" + uuid4().hex
        duration_ms = round((time.perf_counter() - started_clock) * 1000, 3)
        return OfflineEvaluationReport(
            versions=versions,
            sample_references=inputs,
            ablations=ablations,
            threshold_calibration=calibration,
            run_started_at=started_at,
            duration_ms=duration_ms,
            result_id=result_id,
            fixture_notice=dataset.fixture_notice,
        )

    def _run_ablation(
        self,
        dataset: OfflineDataset,
        mode: AblationMode,
        metric_annotations: tuple[RequirementAnnotation, ...],
    ) -> AblationReport:
        use_graph = mode in {"deterministic_plus_graph", "full_fusion"}
        use_semantic = mode in {"deterministic_plus_semantic", "full_fusion"}
        evaluations: list[MatchEvaluation] = []
        labeled: list[tuple[EvaluationDimension, EvaluationLabel, EvaluationLabel]] = []
        recommendations: list[bool] = []
        hard_gates: list[bool] = []
        rejected = 0
        for sample in dataset.samples:
            relation_source = (
                InMemorySkillRelationSource(sample.skill_relations) if use_graph else None
            )
            service = MatchEvaluationService(
                config=self._matching,
                relation_source=relation_source,
                scoring_config=self._scoring,
            )
            evaluation = service.evaluate(
                {
                    "cv_profile": sample.cv_profile.model_dump(mode="json"),
                    "position_profile": sample.position_profile.model_dump(mode="json"),
                }
            )
            evaluations.append(evaluation)
            if evaluation.evaluation_status != "completed":
                rejected += 1
                predictions = {}
            else:
                predictions = _predictions(evaluation)
            for annotation in sample.annotations:
                predicted = predictions.get(annotation.requirement_id, (None, "unknown"))[1]
                labeled.append((annotation.dimension, annotation.label, predicted))
            final = evaluation.final_match_result
            if final is not None:
                recommendations.append(
                    final.recommendation_level == sample.expected_recommendation
                )
                hard_gates.append(
                    final.hard_gate_status == sample.expected_hard_gate_status
                )
        pairs = tuple((actual, predicted) for _, actual, predicted in labeled)
        return AblationReport(
            mode=mode,
            overall_metrics=binary_metrics(pairs),
            confusion_matrix=confusion_matrix(pairs),
            top_k_recall=top_k_recall(metric_annotations, enabled=use_semantic),
            mean_reciprocal_rank=mean_reciprocal_rank(
                metric_annotations, enabled=use_semantic
            ),
            dimension_metrics=dimension_metrics(labeled),
            uncertainty_coverage=uncertainty_coverage(evaluations),
            recommendation_accuracy=(
                round(sum(recommendations) / len(recommendations), 6)
                if recommendations
                else None
            ),
            hard_gate_accuracy=(
                round(sum(hard_gates) / len(hard_gates), 6) if hard_gates else None
            ),
            completed_samples=len(dataset.samples) - rejected,
            rejected_samples=rejected,
        )
