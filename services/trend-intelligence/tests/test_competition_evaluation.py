from copy import deepcopy
from pathlib import Path

import pytest

from app.application.competition_evaluation import (
    DATASET_VERSION,
    REPORT_VERSION,
    evaluate_fixed_dataset,
    load_fixed_dataset,
    precision_at_k,
    render_markdown,
    spearman_rank_correlation,
    validate_fixed_dataset,
)


DATASET = Path(__file__).resolve().parents[1] / "evaluation" / f"{DATASET_VERSION}.json"


def test_ranking_metric_formulas_and_boundaries():
    assert precision_at_k(["a", "b"], ["a", "c"], 2) == 0.5
    assert spearman_rank_correlation(["a", "b", "c"], ["a", "b", "c"])[
        "value"
    ] == 1.0
    assert spearman_rank_correlation(["c", "b", "a"], ["a", "b", "c"])[
        "value"
    ] == -1.0
    unavailable = spearman_rank_correlation(["a"], ["a"])
    assert unavailable == {
        "status": "unavailable",
        "value": None,
        "reason": "fewer_than_two_ranked_items",
    }
    with pytest.raises(ValueError, match="above zero"):
        precision_at_k([], [], 0)


def test_fixed_dataset_calls_real_trend_ranking_and_frequency_baseline():
    report = evaluate_fixed_dataset(load_fixed_dataset(DATASET))

    assert report["report_version"] == REPORT_VERSION
    assert report["algorithm_versions"] == {
        "current": "credibility-weighted-ranking.v1",
        "baseline": "keyword-frequency.v1",
    }
    case = report["cases"][0]
    assert set(case) >= {
        "human_annotations",
        "evaluation_rules",
        "model_results",
        "rule_results",
        "metric_results",
    }
    current = case["metric_results"]["current_algorithm"]
    baseline = case["metric_results"]["frequency_baseline"]
    assert current["k"] == baseline["k"] == 2
    assert len(case["model_results"]["current_algorithm"]["windows"]) == 3
    assert current["precision_at_k"] > baseline["precision_at_k"]
    assert current["spearman"]["value"] > baseline["spearman"]["value"]
    assert case["model_results"]["current_algorithm"]["overall_ranking"][0][
        "source_contributions"
    ]


def test_leading_time_and_source_coverage_include_unavailable_case():
    report = evaluate_fixed_dataset(load_fixed_dataset(DATASET))
    metrics = report["cases"][0]["metric_results"]
    leading = metrics["leading_time"]
    coverage = metrics["source_coverage"]["overall"]

    assert leading["available_count"] == 2
    assert leading["unavailable_count"] == 1
    assert "positive days" in leading["definition"]
    unavailable = next(item for item in leading["items"] if item["status"] == "unavailable")
    assert unavailable["days"] is None
    assert unavailable["reason"]
    assert coverage["valid_source_count"] == 4
    assert coverage["source_type_count"] == 4
    assert coverage["enterprise_count"] >= 4
    assert 0.0 <= coverage["top_k_multi_source_evidence_ratio"] <= 1.0


def test_report_is_reproducible_and_markdown_has_required_sections():
    dataset = load_fixed_dataset(DATASET)
    first = evaluate_fixed_dataset(dataset)
    second = evaluate_fixed_dataset(dataset)

    assert first == second
    markdown = render_markdown(first)
    for text in (
        "总体指标",
        "每窗口排名",
        "领先时间",
        "来源覆盖",
        "关键词频次基线",
        "代表性案例与失败原因",
        "unavailable",
    ):
        assert text in markdown


def test_fixed_dataset_validation_reports_case_and_field():
    dataset = load_fixed_dataset(DATASET)
    invalid = deepcopy(dataset)
    del invalid["cases"][0]["expected"]

    with pytest.raises(ValueError, match=r"case\[0\].*expected"):
        validate_fixed_dataset(invalid)
