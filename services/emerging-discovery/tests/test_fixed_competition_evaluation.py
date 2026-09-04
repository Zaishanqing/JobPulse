from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.api.competition_evaluation import (
    DATASET_VERSION,
    REPORT_VERSION,
    evaluate_fixed_dataset,
    load_fixed_dataset,
    persistence_metrics,
    precision_at_k,
    render_markdown,
    validate_fixed_dataset,
)
from app.api.contracts import FixedCompetitionEvaluationRequest
from app.infrastructure.algorithm_registry import AlgorithmRegistry


DATASET = Path(__file__).resolve().parents[1] / "evaluation" / f"{DATASET_VERSION}.json"


def _registry() -> AlgorithmRegistry:
    return AlgorithmRegistry()


def test_metric_calculation_has_explicit_precision_and_persistence_formulas():
    assert precision_at_k(["a", "b"], {"a", "c"}, 2) == 0.5
    persistence = persistence_metrics([{"skills:a"}, {"skills:a", "skills:b"}])
    assert persistence["consecutive_top_k_jaccard"] == [0.5]
    assert persistence["candidate_persistence_rate"] == 0.5
    assert "candidate_persistence_rate" in persistence["formula"]


def test_fixed_entry_calls_real_algorithms_and_compares_same_windows_and_k():
    report = evaluate_fixed_dataset(load_fixed_dataset(DATASET), _registry())

    assert report["report_version"] == REPORT_VERSION
    assert report["algorithm_versions"]["current"].startswith("multi_view:")
    assert report["algorithm_versions"]["baseline"].startswith("baseline:")
    assert isinstance(report["execution_context"]["semantic_provider_available"], bool)
    case = report["cases"][0]
    assert set(case) >= {
        "human_expected",
        "evaluation_rules",
        "model_results",
        "baseline_results",
        "metric_results",
    }
    metrics = case["metric_results"]
    current = metrics["multi_view"]
    baseline = metrics["baseline"]
    assert current["k"] == baseline["k"] == 2
    assert [item["window_id"] for item in current["windows"]] == [
        item["window_id"] for item in baseline["windows"]
    ]
    assert len(current["windows"]) == 3
    assert case["model_results"]["multi_view"]["overall"]["clusters"]
    assert case["baseline_results"]["baseline"]["overall"]["clusters"]
    assert any(
        item["result"]["enterprise_debias"]["without_top_enterprise"]["cluster_count"]
        == 0
        for item in case["model_results"]["multi_view"]["windows"]
    )
    assert current["overall_precision_at_k"] > baseline["overall_precision_at_k"]
    assert metrics["comparison"]["precision_at_k_delta"] == round(
        current["overall_precision_at_k"] - baseline["overall_precision_at_k"], 6
    )
    assert metrics["comparison"]["mean_consecutive_jaccard_delta"] == round(
        current["cross_window_persistence"]["mean_consecutive_top_k_jaccard"]
        - baseline["cross_window_persistence"]["mean_consecutive_top_k_jaccard"],
        6,
    )


def test_report_is_deterministic_and_markdown_contains_required_sections():
    dataset = load_fixed_dataset(DATASET)
    first = evaluate_fixed_dataset(dataset, _registry())
    second = evaluate_fixed_dataset(dataset, _registry())

    assert first == second
    markdown = render_markdown(first)
    for text in (
        "总体 Precision@K",
        "每窗口结果",
        "跨窗口持续性公式",
        "Top-K 明细",
        "false positive",
        "false negative",
        "失败原因",
    ):
        assert text in markdown


def test_fixed_dataset_validation_reports_case_and_field():
    dataset = load_fixed_dataset(DATASET)
    invalid = deepcopy(dataset)
    del invalid["cases"][0]["source_metadata"]

    with pytest.raises(ValueError, match=r"case\[0\].*source_metadata"):
        validate_fixed_dataset(invalid)


def test_public_evaluation_request_rejects_caller_supplied_expected_or_actual():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        FixedCompetitionEvaluationRequest.model_validate(
            {
                "dataset_version": DATASET_VERSION,
                "expected": {"candidate": "caller-controlled"},
                "actual": {"candidate": "caller-controlled"},
            }
        )
