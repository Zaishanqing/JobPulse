"""Pure metric calculations for offline matching evaluation."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from app.domain.evaluation import MatchEvaluation
from app.evaluation.models import (
    BinaryMetrics,
    ConfusionCell,
    DimensionMetrics,
    EvaluationDimension,
    EvaluationLabel,
    RequirementAnnotation,
    ThresholdCalibrationReport,
    ThresholdCandidateReport,
    TopKRecall,
    UncertaintyCoverage,
)

LABELS: tuple[EvaluationLabel, ...] = (
    "matched",
    "partial",
    "not_matched",
    "unknown",
)
DIMENSIONS: tuple[EvaluationDimension, ...] = (
    "hard_constraint",
    "required_skill",
    "bonus_skill",
    "responsibility",
    "project",
    "scenario",
)
_POSITIVE = frozenset({"matched", "partial"})


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def binary_metrics(
    pairs: Iterable[tuple[EvaluationLabel, EvaluationLabel]],
) -> BinaryMetrics:
    known = tuple((actual, predicted) for actual, predicted in pairs if actual != "unknown")
    true_positive = sum(
        actual in _POSITIVE and predicted in _POSITIVE for actual, predicted in known
    )
    false_positive = sum(
        actual == "not_matched" and predicted in _POSITIVE
        for actual, predicted in known
    )
    false_negative = sum(
        actual in _POSITIVE and predicted not in _POSITIVE
        for actual, predicted in known
    )
    precision = _ratio(true_positive, true_positive + false_positive)
    recall = _ratio(true_positive, true_positive + false_negative)
    f1 = (
        round(2 * precision * recall / (precision + recall), 6)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    return BinaryMetrics(
        precision=precision,
        recall=recall,
        f1=f1,
        true_positive=true_positive,
        false_positive=false_positive,
        false_negative=false_negative,
        support=len(known),
    )


def confusion_matrix(
    pairs: Iterable[tuple[EvaluationLabel, EvaluationLabel]],
) -> tuple[ConfusionCell, ...]:
    counts = Counter(pairs)
    return tuple(
        ConfusionCell(actual=actual, predicted=predicted, count=counts[actual, predicted])
        for actual in LABELS
        for predicted in LABELS
    )


def dimension_metrics(
    labeled_predictions: Iterable[
        tuple[EvaluationDimension, EvaluationLabel, EvaluationLabel]
    ],
) -> tuple[DimensionMetrics, ...]:
    values = tuple(labeled_predictions)
    return tuple(
        DimensionMetrics(
            dimension=dimension,
            metrics=binary_metrics(
                (actual, predicted)
                for item_dimension, actual, predicted in values
                if item_dimension == dimension
            ),
            support=sum(item_dimension == dimension for item_dimension, _, _ in values),
        )
        for dimension in DIMENSIONS
    )


def top_k_recall(
    annotations: Iterable[RequirementAnnotation],
    *,
    enabled: bool,
    ks: tuple[int, ...] = (1, 3, 5),
) -> tuple[TopKRecall, ...]:
    relevant = tuple(
        item
        for item in annotations
        if item.label in _POSITIVE and item.relevant_rank is not None
    )
    if not enabled:
        relevant = ()
    return tuple(
        TopKRecall(
            k=k,
            recall=(
                _ratio(sum(item.relevant_rank <= k for item in relevant), len(relevant))
                if relevant
                else None
            ),
            eligible_count=len(relevant),
        )
        for k in ks
    )


def mean_reciprocal_rank(
    annotations: Iterable[RequirementAnnotation], *, enabled: bool
) -> float | None:
    if not enabled:
        return None
    ranks = tuple(
        item.relevant_rank
        for item in annotations
        if item.label in _POSITIVE and item.relevant_rank is not None
    )
    return round(sum(1 / rank for rank in ranks) / len(ranks), 6) if ranks else None


def uncertainty_coverage(
    evaluations: Iterable[MatchEvaluation],
) -> UncertaintyCoverage:
    statuses: list[str] = []
    for evaluation in evaluations:
        statuses.extend(item.status for item in evaluation.hard_constraint_results)
        statuses.extend(item.match_status for item in evaluation.skill_results)
        statuses.extend(item.match_status for item in evaluation.responsibility_results)
        statuses.extend(item.match_status for item in evaluation.project_results)
        statuses.extend(item.match_status for item in evaluation.scenario_results)
    total = len(statuses)
    unknown = statuses.count("unknown")
    unresolved = statuses.count("unresolved")
    return UncertaintyCoverage(
        total_results=total,
        unknown_count=unknown,
        unresolved_count=unresolved,
        unknown_rate=_ratio(unknown, total) or 0.0,
        unresolved_rate=_ratio(unresolved, total) or 0.0,
    )


def calibrate_thresholds(
    annotations: Iterable[RequirementAnnotation],
    thresholds: tuple[float, ...],
) -> ThresholdCalibrationReport:
    if not thresholds or any(value < 0 or value > 1 for value in thresholds):
        raise ValueError("semantic thresholds must be a non-empty set within 0..1")
    candidates = tuple(sorted(set(thresholds)))
    scored = tuple(item for item in annotations if item.semantic_score is not None)
    reports = tuple(
        ThresholdCandidateReport(
            threshold=threshold,
            metrics=binary_metrics(
                (
                    item.label,
                    "partial" if item.semantic_score >= threshold else "not_matched",
                )
                for item in scored
            ),
        )
        for threshold in candidates
    )

    def rank(item: ThresholdCandidateReport) -> tuple[float, float, float, float]:
        metrics = item.metrics
        return (
            metrics.f1 if metrics.f1 is not None else -1.0,
            metrics.precision if metrics.precision is not None else -1.0,
            metrics.recall if metrics.recall is not None else -1.0,
            item.threshold,
        )

    recommended = max(reports, key=rank).threshold if scored else None
    return ThresholdCalibrationReport(
        candidates=reports,
        recommended_threshold=recommended,
    )
