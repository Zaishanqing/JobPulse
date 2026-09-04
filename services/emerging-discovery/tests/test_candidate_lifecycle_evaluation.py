from copy import deepcopy
from pathlib import Path

import pytest

from app.application.candidate_lifecycle_evaluation import (
    DATASET_VERSION,
    REPORT_VERSION,
    case_metrics,
    evaluate_dataset,
    load_dataset,
    render_markdown,
    validate_dataset,
)


DATASET = Path(__file__).resolve().parents[1] / "evaluation" / f"{DATASET_VERSION}.json"


def test_dataset_schema_validation_reports_missing_field():
    dataset = load_dataset(DATASET)
    invalid = deepcopy(dataset)
    del invalid["cases"][0]["windows"]

    with pytest.raises(ValueError, match=r"case\[0\].*windows"):
        validate_dataset(invalid)


def test_evaluation_is_deterministic_and_all_methods_execute():
    dataset = load_dataset(DATASET)
    first = evaluate_dataset(dataset)
    second = evaluate_dataset(dataset)

    assert first == second
    assert first["report_version"] == REPORT_VERSION
    assert set(first["metrics"]) == {
        "full",
        "baseline_exact_title",
        "baseline_title_similarity",
        "ablation_no_title",
        "ablation_no_skill",
        "ablation_no_responsibility",
        "ablation_no_membership",
    }
    for method in (
        "full",
        "baseline_exact_title",
        "baseline_title_similarity",
    ):
        assert first["metrics"][method]["identity_f1"] is not None


def test_ablation_executes_and_reports_component_importance():
    report = evaluate_dataset(load_dataset(DATASET))
    ablation = report["ablation"]

    assert set(ablation) >= {
        "full",
        "no_title",
        "no_skill",
        "no_responsibility",
        "no_membership",
    }
    assert ablation["no_title"]["identity_f1"] is not None
    assert ablation["no_skill"]["identity_f1"] is not None


def test_identity_and_lifecycle_metrics_are_calculated_from_known_counts():
    observations = [
        {
            "expected_candidate_id": "A",
            "predicted_candidate_id": "p1",
            "window_index": 0,
        },
        {
            "expected_candidate_id": "A",
            "predicted_candidate_id": "p2",
            "window_index": 1,
        },
        {
            "expected_candidate_id": "B",
            "predicted_candidate_id": "p2",
            "window_index": 2,
        },
    ]
    states = [
        {
            "window_id": "w1",
            "expected_candidate_id": "A",
            "expected_state": "weak_signal",
            "predicted_state": "weak_signal",
            "available": True,
        },
        {
            "window_id": "w1",
            "expected_candidate_id": "B",
            "expected_state": "weak_signal",
            "predicted_state": "incubating",
            "available": True,
        },
    ]
    metrics = case_metrics(observations, states)

    assert metrics["tp"] == 0
    assert metrics["fp"] == 1
    assert metrics["fn"] == 1
    assert metrics["tn"] == 1
    assert metrics["false_new_errors"] == 1
    assert metrics["false_merge_errors"] == 1
    assert metrics["lifecycle_correct"] == 1
    assert metrics["lifecycle_total"] == 2


def test_failure_cases_are_emitted_and_markdown_has_required_sections():
    report = evaluate_dataset(load_dataset(DATASET))
    assert all(isinstance(case["failure_cases"], list) for case in report["cases"])
    assert any(case["failure_cases"] for case in report["cases"])
    markdown = render_markdown(report)
    for section in ("## Dataset", "## Baseline", "## Full Method", "## Metrics", "## Ablation", "## Failure Cases", "## Limitations"):
        assert section in markdown
