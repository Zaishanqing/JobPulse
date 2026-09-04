from dataclasses import dataclass
from typing import Mapping

from app.domain.errors import PermissionDenied


EVALUATION_ADMIN_ROLES = frozenset({"admin", "developer"})


class EvaluationRuleViolation(ValueError):
    pass


def require_evaluation_admin(role: str) -> None:
    if role not in EVALUATION_ADMIN_ROLES:
        raise PermissionDenied("No permission to manage evaluation data")


@dataclass(frozen=True)
class EvaluationMetrics:
    metric_name: str
    metric_value: float | None
    evaluated_count: int
    error_count: int
    skipped_count: int
    evaluation_status: str
    algorithm_version: str
    implementation_status: str
    correct_count: int | None = None
    cluster_count: int | None = None

    def as_dict(self) -> dict[str, object]:
        values: dict[str, object] = {
            self.metric_name: self.metric_value,
            "evaluated_count": self.evaluated_count,
            "error_count": self.error_count,
            "skipped_count": self.skipped_count,
            "evaluation_status": self.evaluation_status,
            "algorithm_version": self.algorithm_version,
            "implementation_status": self.implementation_status,
        }
        if self.correct_count is not None:
            values["correct_count"] = self.correct_count
        if self.cluster_count is not None:
            values["cluster_count"] = self.cluster_count
        return values

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "EvaluationMetrics":
        reserved = {
            "evaluated_count", "correct_count", "error_count", "skipped_count",
            "evaluation_status", "algorithm_version", "implementation_status",
            "cluster_count",
        }
        metric_name = next((key for key in values if key not in reserved), "metric")
        metric_value = values.get(metric_name)
        return cls(
            metric_name=metric_name,
            metric_value=float(metric_value) if isinstance(metric_value, int | float) else None,
            evaluated_count=int(values.get("evaluated_count", 0)),
            error_count=int(values.get("error_count", 0)),
            skipped_count=int(values.get("skipped_count", 0)),
            evaluation_status=str(values.get("evaluation_status", "insufficient_data")),
            algorithm_version=str(values.get("algorithm_version", "")),
            implementation_status=str(values.get("implementation_status", "data_driven_rule_evaluation")),
            correct_count=int(values["correct_count"]) if "correct_count" in values else None,
            cluster_count=int(values["cluster_count"]) if "cluster_count" in values else None,
        )


@dataclass(frozen=True)
class EvaluationErrorCase:
    case_id: str
    error_type: str
    description: str
    expected: object | None = None
    actual: object | None = None

    def as_dict(self) -> dict[str, object]:
        values: dict[str, object] = {
            "case_id": self.case_id,
            "type": self.error_type,
            "description": self.description,
        }
        if self.error_type == "mismatch":
            values.update(expected=self.expected, actual=self.actual)
        return values

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "EvaluationErrorCase":
        return cls(
            case_id=str(values.get("case_id", "")),
            error_type=str(values.get("type", "unknown")),
            description=str(values.get("description", "")),
            expected=values.get("expected"),
            actual=values.get("actual"),
        )


@dataclass(frozen=True)
class EvaluationConfigSnapshot:
    comparison: str | None = None
    numeric_tolerance: float | None = None
    metric: str | None = None
    input_fields: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        if self.comparison is not None:
            return {
                "comparison": self.comparison,
                "numeric_tolerance": self.numeric_tolerance,
            }
        return {"metric": self.metric, "input_fields": list(self.input_fields)}

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "EvaluationConfigSnapshot":
        fields = values.get("input_fields", ())
        return cls(
            comparison=str(values["comparison"]) if values.get("comparison") is not None else None,
            numeric_tolerance=float(values["numeric_tolerance"]) if values.get("numeric_tolerance") is not None else None,
            metric=str(values["metric"]) if values.get("metric") is not None else None,
            input_fields=tuple(str(item) for item in fields) if isinstance(fields, list | tuple) else (),
        )


@dataclass(frozen=True)
class EvaluationOutcome:
    metrics: EvaluationMetrics
    error_cases: tuple[EvaluationErrorCase, ...]
    status: str
    algorithm_version: str
    config_snapshot: EvaluationConfigSnapshot
    evaluated_count: int
    error_count: int


def _normalize(value: object) -> object:
    if isinstance(value, str):
        return " ".join(value.strip().lower().split())
    if isinstance(value, list):
        return sorted((_normalize(item) for item in value), key=repr)
    if isinstance(value, dict):
        return {key: _normalize(value[key]) for key in sorted(value)}
    return value


def _matches(expected: object, actual: object, tolerance: float) -> bool:
    if isinstance(expected, int | float) and isinstance(actual, int | float):
        return abs(float(expected) - float(actual)) <= tolerance
    return _normalize(expected) == _normalize(actual)


def evaluate_cases(
    items: object,
    metric_name: str,
    algorithm_version: str,
    tolerance: float = 0.0,
) -> EvaluationOutcome:
    cases = items if isinstance(items, list) else []
    evaluated = correct = skipped = 0
    errors: list[EvaluationErrorCase] = []
    for index, item in enumerate(cases):
        case_id = item.get("case_id", f"case_{index + 1:03d}") if isinstance(item, dict) else f"case_{index + 1:03d}"
        if not isinstance(item, dict) or "expected" not in item or "actual" not in item:
            skipped += 1
            errors.append(EvaluationErrorCase(str(case_id), "not_evaluable", "Case requires expected and actual values"))
            continue
        evaluated += 1
        if _matches(item["expected"], item["actual"], tolerance):
            correct += 1
        else:
            errors.append(EvaluationErrorCase(str(case_id), "mismatch", "Actual result differs from expected result", item["expected"], item["actual"]))
    status = "completed" if evaluated else "insufficient_data"
    error_count = evaluated - correct
    metrics = EvaluationMetrics(
        metric_name, round(correct / evaluated, 4) if evaluated else None,
        evaluated, error_count, skipped, status, algorithm_version,
        "data_driven_rule_evaluation", correct_count=correct,
    )
    return EvaluationOutcome(
        metrics, tuple(errors), status, algorithm_version,
        EvaluationConfigSnapshot("normalized_exact_or_numeric_tolerance", tolerance),
        evaluated, error_count,
    )


def evaluate_clusters(items: object) -> EvaluationOutcome:
    cases = items if isinstance(items, list) else []
    clusters: dict[str, list[str]] = {}
    errors: list[EvaluationErrorCase] = []
    for index, item in enumerate(cases):
        if not isinstance(item, dict) or "expected_label" not in item or "actual_cluster" not in item:
            errors.append(EvaluationErrorCase(str(item.get("case_id", index)) if isinstance(item, dict) else str(index), "invalid_case", "Case requires expected_label and actual_cluster"))
            continue
        clusters.setdefault(str(item["actual_cluster"]), []).append(str(item["expected_label"]))
    evaluated = sum(len(labels) for labels in clusters.values())
    majority = sum(max(labels.count(label) for label in set(labels)) for labels in clusters.values())
    status = "completed" if evaluated else "insufficient_data"
    metrics = EvaluationMetrics(
        "cluster_purity", round(majority / evaluated, 4) if evaluated else None,
        evaluated, len(errors), len(errors), status, "cluster-purity-v1",
        "data_driven_cluster_evaluation", cluster_count=len(clusters),
    )
    return EvaluationOutcome(
        metrics, tuple(errors), status, "cluster-purity-v1",
        EvaluationConfigSnapshot(metric="weighted_cluster_majority_purity", input_fields=("expected_label", "actual_cluster")),
        evaluated, len(errors),
    )
