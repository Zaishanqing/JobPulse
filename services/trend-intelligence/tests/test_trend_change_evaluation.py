from pathlib import Path

import pytest

from app.application.trend_change_evaluation import (
    DATASET_VERSION,
    evaluate_change_dataset,
    load_change_dataset,
    validate_change_dataset,
)


DATASET = Path(__file__).resolve().parents[1] / "evaluation" / f"{DATASET_VERSION}.json"


def test_change_evaluation_dataset_covers_all_required_cases():
    dataset = load_change_dataset(DATASET)

    assert {case["case_id"] for case in dataset["cases"]} == {
        "stable",
        "sudden-rise",
        "slow-growth",
        "decline",
        "noise-spike",
        "volatile",
    }


def test_change_evaluation_passes_every_case_deterministically():
    dataset = load_change_dataset(DATASET)

    first = evaluate_change_dataset(dataset)
    second = evaluate_change_dataset(dataset)

    assert first == second
    assert first["case_count"] == 6
    assert first["passed_case_count"] == first["case_count"]


def test_change_dataset_validation_reports_case_and_field():
    dataset = load_change_dataset(DATASET)
    del dataset["cases"][0]["expected_trend_state"]

    with pytest.raises(ValueError, match=r"case\[0\].*expected_trend_state"):
        validate_change_dataset(dataset)
