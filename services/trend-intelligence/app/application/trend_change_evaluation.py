from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.domain.trend_change import (
    DEFAULT_ALGORITHM_VERSION,
    TrendWindowScore,
    analyze_trend_series,
)


DATASET_VERSION = "trend-change-cases.v1"
TREND_STATES = frozenset({"rising", "accelerating", "stable", "declining", "volatile"})


def load_change_dataset(path: Path) -> dict[str, Any]:
    try:
        dataset = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"trend change evaluation dataset not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"trend change evaluation dataset is not valid JSON: {exc}"
        ) from exc
    validate_change_dataset(dataset)
    return dataset


def validate_change_dataset(dataset: object) -> None:
    if not isinstance(dataset, dict):
        raise ValueError("trend change evaluation dataset root must be an object")
    if dataset.get("dataset_version") != DATASET_VERSION:
        raise ValueError(f"dataset_version must be {DATASET_VERSION}")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases must be a non-empty array")
    seen: set[str] = set()
    required = {
        "case_id",
        "subject_id",
        "subject_type",
        "scores",
        "expected_trend_state",
        "expected_change_point_windows",
    }
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"case[{index}] must be an object")
        missing = sorted(required - set(case))
        if missing:
            raise ValueError(
                f"case[{index}] missing required fields: {', '.join(missing)}"
            )
        case_id = str(case["case_id"]).strip()
        if not case_id or case_id in seen:
            raise ValueError(f"case[{index}] case_id must be non-empty and unique")
        seen.add(case_id)
        scores = case["scores"]
        if (
            not isinstance(scores, list)
            or len(scores) < 2
            or not all(isinstance(value, (int, float)) for value in scores)
        ):
            raise ValueError(
                f"case {case_id} scores must be a numeric array with at least two values"
            )
        if case["expected_trend_state"] not in TREND_STATES:
            raise ValueError(f"case {case_id} expected_trend_state is invalid")
        if not isinstance(case["expected_change_point_windows"], list):
            raise ValueError(
                f"case {case_id} expected_change_point_windows must be a list"
            )


def evaluate_change_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    validate_change_dataset(dataset)
    case_results = []
    algorithm_version = str(dataset.get("algorithm_version") or DEFAULT_ALGORITHM_VERSION)
    for case in dataset["cases"]:
        windows = [
            TrendWindowScore(
                subject_id=str(case["subject_id"]),
                subject_type=str(case["subject_type"]),
                window=f"w{index + 1}",
                score=float(score),
            )
            for index, score in enumerate(case["scores"])
        ]
        analysis = analyze_trend_series(
            str(case["subject_id"]),
            str(case["subject_type"]),
            windows,
            algorithm_version=algorithm_version,
        )
        actual_windows = [point.change_point_window for point in analysis.change_points]
        failures = []
        if analysis.trend_state != case["expected_trend_state"]:
            failures.append(
                f"trend_state={analysis.trend_state}, "
                f"expected={case['expected_trend_state']}"
            )
        if actual_windows != case["expected_change_point_windows"]:
            failures.append(
                f"change_points={actual_windows}, "
                f"expected={case['expected_change_point_windows']}"
            )
        case_results.append(
            {
                "case_id": case["case_id"],
                "passed": not failures,
                "failures": failures,
                "actual_trend_state": analysis.trend_state,
                "actual_change_point_windows": actual_windows,
            }
        )
    return {
        "dataset_version": dataset["dataset_version"],
        "algorithm_version": algorithm_version,
        "case_count": len(case_results),
        "passed_case_count": sum(item["passed"] for item in case_results),
        "cases": case_results,
    }
