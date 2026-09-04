from copy import deepcopy
from pathlib import Path

import pytest

from app.application.trend_change_competition_evaluation import (
    CP_TOLERANCE_WINDOWS,
    DATASET_VERSION,
    REPORT_VERSION,
    adjacent_delta_baseline,
    change_point_metrics,
    evaluate_dataset,
    load_dataset,
    render_markdown,
    validate_dataset,
)


DATASET = Path(__file__).resolve().parents[1] / "evaluation" / f"{DATASET_VERSION}.json"


def test_dataset_schema_validation_reports_missing_field():
    dataset = load_dataset(DATASET)
    invalid = deepcopy(dataset)
    del invalid["cases"][0]["expected_trend_state"]

    with pytest.raises(ValueError, match=r"case\[0\].*expected_trend_state"):
        validate_dataset(invalid)


def test_evaluation_is_deterministic_and_covers_required_scenarios():
    dataset = load_dataset(DATASET)
    first = evaluate_dataset(dataset)
    second = evaluate_dataset(dataset)

    assert first == second
    assert first["report_version"] == REPORT_VERSION
    assert {case["case_id"] for case in dataset["cases"]} == {
        "stable",
        "sudden-rise",
        "slow-growth",
        "decline",
        "noise-spike",
        "volatile",
        "accelerating",
        "multiple-change-points",
        "irregular-window-length",
    }
    assert CP_TOLERANCE_WINDOWS == 1


def test_change_point_metrics_use_one_window_tolerance():
    metrics = change_point_metrics(
        ["w2", "w5"],
        ["w3", "w5"],
        ["w1", "w2", "w3", "w4", "w5"],
    )
    assert metrics["tp"] == 2
    assert metrics["fp"] == 0
    assert metrics["fn"] == 0
    assert metrics["f1"] == 1.0
    assert metrics["detection_delay_windows"] == 0.5

    false_alarm = change_point_metrics(
        [], ["w3"], ["w1", "w2", "w3", "w4", "w5"]
    )
    assert false_alarm["fp"] == 1
    assert false_alarm["false_alarm_rate"] == 1.0


def test_baseline_and_full_execute_with_distinct_change_points():
    dataset = load_dataset(DATASET)
    report = evaluate_dataset(dataset)
    irregular = next(
        case for case in report["cases"] if case["case_id"] == "irregular-window-length"
    )
    assert irregular["model_results"]["full"]["change_point_windows"] == []
    assert irregular["model_results"]["baseline"]["change_point_windows"] != []
    baseline = adjacent_delta_baseline(
        [float(value) for value in dataset["cases"][0]["scores"]],
        ["w1", "w2", "w3", "w4", "w5"],
    )
    assert baseline["method"] == "adjacent-delta-threshold.v1"


def test_failure_cases_and_markdown_sections_are_present():
    report = evaluate_dataset(load_dataset(DATASET))
    assert any(case["failure_cases"] for case in report["cases"])
    markdown = render_markdown(report)
    for section in ("## Dataset", "## Baseline", "## Full Method", "## Metrics", "## Failure Cases", "## Limitations"):
        assert section in markdown
