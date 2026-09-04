from copy import deepcopy
from pathlib import Path

import pytest

from app.application.evolution_evaluation import (
    DATASET_VERSION,
    NOT_COVERED,
    REPORT_VERSION,
    evaluate_dataset,
    event_metrics,
    load_dataset,
    render_markdown,
    snapshot_diff_baseline,
    validate_dataset,
)


DATASET = Path(__file__).resolve().parents[1] / "evaluation" / f"{DATASET_VERSION}.json"


def test_dataset_schema_validation_reports_missing_field():
    dataset = load_dataset(DATASET)
    invalid = deepcopy(dataset)
    del invalid["cases"][0]["expected_events"]

    with pytest.raises(ValueError, match=r"case\[0\].*expected_events"):
        validate_dataset(invalid)


def test_evaluation_is_deterministic_and_both_methods_execute():
    dataset = load_dataset(DATASET)
    first = evaluate_dataset(dataset)
    second = evaluate_dataset(dataset)

    assert first == second
    assert first["report_version"] == REPORT_VERSION
    assert set(first["metrics"]) == {"full", "baseline"}
    assert first["metrics"]["full"]["event_f1"] is not None
    assert first["metrics"]["baseline"]["event_f1"] is not None


def test_event_metrics_suppress_atomic_evidence_for_matched_composite():
    expected = [
        {
            "event_type": "skill_replacement",
            "source_keys": ["TF"],
            "target_keys": ["PT"],
        }
    ]
    predicted = [
        {
            "event_type": "skill_replacement",
            "source_keys": ["TF"],
            "target_keys": ["PT"],
        },
        {
            "event_type": "skill_emergence",
            "source_keys": [],
            "target_keys": ["PT"],
        },
        {
            "event_type": "skill_decline",
            "source_keys": ["TF"],
            "target_keys": [],
        },
    ]
    full_metrics = event_metrics(expected, predicted, suppress_evidence=True)
    baseline_metrics = event_metrics(expected, predicted, suppress_evidence=False)

    assert full_metrics["tp"] == 1
    assert full_metrics["fp"] == 0
    assert full_metrics["event_f1"] == 1.0
    assert full_metrics["replacement_f1"] == 1.0
    assert baseline_metrics["tp"] == 1
    assert baseline_metrics["fp"] == 2
    assert baseline_metrics["event_precision"] == pytest.approx(1 / 3)


def test_baseline_cannot_recover_replacement_semantics():
    dataset = load_dataset(DATASET)
    replacement_case = next(
        case for case in dataset["cases"] if case["case_id"] == "skill-replacement"
    )
    events = snapshot_diff_baseline(
        replacement_case["snapshots"]["before"],
        replacement_case["snapshots"]["after"],
        position_id=replacement_case["position_id"],
        from_version_id=replacement_case["from_version_id"],
        to_version_id=replacement_case["to_version_id"],
    )
    assert not any(item["event_type"] == "skill_replacement" for item in events)
    assert any(item["event_type"] == "role_expansion" for item in events)


def test_failure_cases_and_not_covered_are_explicit():
    report = evaluate_dataset(load_dataset(DATASET))
    assert NOT_COVERED == ["position_split", "position_merge"]
    assert any(
        case["failure_cases"]["snapshot_diff_baseline"] for case in report["cases"]
    )
    markdown = render_markdown(report)
    for section in ("## Dataset", "## Baseline", "## Full Method", "## Metrics", "## Failure Cases", "## Limitations"):
        assert section in markdown
