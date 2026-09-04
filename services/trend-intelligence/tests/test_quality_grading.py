from __future__ import annotations

from app.domain.market import compute_source_quality_grade


def test_quality_grade_good():
    dimensions = {
        "time_window_coverage": 90.0,
        "sample_sufficiency": 85.0,
        "data_freshness": 95.0,
        "field_completeness": 88.0,
        "parse_success_rate": 92.0,
        "temporal_distribution": 80.0,
        "source_specific_signal": 70.0,
    }
    score, grade, flags = compute_source_quality_grade("arxiv", dimensions)
    assert grade == "good"
    assert score >= 75
    assert "low_sample" not in flags


def test_quality_grade_degraded():
    dimensions = {
        "time_window_coverage": 55.0,
        "sample_sufficiency": 40.0,
        "data_freshness": 60.0,
        "field_completeness": 65.0,
        "parse_success_rate": 70.0,
        "temporal_distribution": 50.0,
        "source_specific_signal": 30.0,
    }
    score, grade, flags = compute_source_quality_grade("arxiv", dimensions)
    assert grade == "degraded"
    assert 45 <= score < 75


def test_quality_grade_poor():
    dimensions = {
        "time_window_coverage": 20.0,
        "sample_sufficiency": 15.0,
        "data_freshness": 10.0,
        "field_completeness": 30.0,
        "parse_success_rate": 25.0,
        "temporal_distribution": 20.0,
        "source_specific_signal": 10.0,
    }
    score, grade, flags = compute_source_quality_grade("arxiv", dimensions)
    assert grade == "poor"
    assert score < 45
    assert len(flags) >= 3


def test_quality_flags_appended():
    dimensions = {
        "time_window_coverage": 30.0,
        "sample_sufficiency": 100.0,
        "data_freshness": 40.0,
        "field_completeness": 50.0,
        "parse_success_rate": 100.0,
        "temporal_distribution": 100.0,
        "source_specific_signal": 100.0,
    }
    score, grade, flags = compute_source_quality_grade("arxiv", dimensions)
    assert "partial_time_coverage" in flags
    assert "incomplete_fields" in flags
