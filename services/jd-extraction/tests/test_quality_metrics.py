from __future__ import annotations

from src.quality_metrics import best_span_f1, compute_quality_metrics, token_f1


def test_token_f1_exact_and_partial() -> None:
    assert token_f1("负责后端 API 设计", "负责后端 API 设计") == 1.0
    partial = token_f1("负责后端 API 设计", "负责后端 API 设计与开发")
    assert 0.0 < partial < 1.0
    assert token_f1("", "") == 1.0
    assert token_f1("Python", "") == 0.0


def test_best_span_f1_selects_best_predicted_span() -> None:
    assert best_span_f1(["无关内容", "负责后端 API 设计与开发"], "负责后端 API 设计与开发") == 1.0
    assert best_span_f1([], "负责后端 API 设计") == 0.0


def test_compute_quality_metrics_reports_f1_evidence_and_hallucination() -> None:
    predicted = {
        "skills": ["Python", "FastAPI", "Docker"],
        "responsibility_spans": ["负责后端 API 设计与开发"],
        "requirement_spans": ["熟练使用 Python", "熟悉 FastAPI 框架"],
        "evidence_quotes": ["负责后端 API 设计与开发", "熟练使用 Python", "不存在的内容"],
        "schema_failed": False,
    }
    expected = {
        "skills": ["Python", "FastAPI"],
        "responsibilities": ["负责后端 API 设计与开发"],
        "requirements": ["熟练使用 Python", "熟悉 FastAPI 框架", "本科及以上学历"],
    }
    raw_text = "岗位职责：负责后端 API 设计与开发。任职要求：熟练使用 Python，熟悉 FastAPI 框架。"
    metrics = compute_quality_metrics(predicted, expected, raw_text)

    assert metrics["skill_precision"] == round(2 / 3, 4)
    assert metrics["skill_recall"] == 1.0
    assert metrics["skill_f1"] == 0.8
    assert metrics["responsibility_span_f1"] == 1.0
    assert 0.0 < metrics["requirement_span_f1"] < 1.0
    assert metrics["schema_failed"] is False
    assert metrics["evidence_exact_rate"] == round(2 / 3, 4)
    assert metrics["hallucination_cases"] == ["不存在的内容"]


def test_compute_quality_metrics_marks_schema_failure() -> None:
    metrics = compute_quality_metrics(
        {"schema_failed": True},
        {"skills": [], "responsibilities": [], "requirements": []},
        "",
    )
    assert metrics["schema_failed"] is True
    assert metrics["span_f1"] == 1.0
