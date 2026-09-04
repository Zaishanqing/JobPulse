from __future__ import annotations

from dataclasses import asdict

from app.application.trend_change import TrendChangeService
from app.domain.trend_change import (
    DEFAULT_ALGORITHM_VERSION,
    DEFAULT_TREND_CHANGE_CONFIG,
    TrendWindowScore,
    analyze_trend_series,
)


def series(values: list[float], **kwargs) -> list[TrendWindowScore]:
    return [
        TrendWindowScore(
            subject_id="subject-1",
            subject_type="market_signal",
            window=f"w{index + 1}",
            score=value,
            **kwargs,
        )
        for index, value in enumerate(values)
    ]


def test_growth_rate_and_absolute_growth():
    windows = [
        TrendWindowScore("s", "market_signal", "w1", 0.10, duration_days=1),
        TrendWindowScore("s", "market_signal", "w2", 0.15, duration_days=2),
        TrendWindowScore("s", "market_signal", "w3", 0.20, duration_days=3),
    ]
    analysis = analyze_trend_series("s", "market_signal", windows)

    assert analysis.windows[1].absolute_growth == 0.05
    assert analysis.windows[1].growth_rate == 0.025
    assert analysis.windows[2].absolute_growth == 0.05
    assert analysis.windows[2].growth_rate == 0.016667
    assert analysis.windows[1].relative_growth == 0.5
    assert analysis.windows[2].relative_growth == 0.333333


def test_acceleration():
    analysis = analyze_trend_series("s", "market_signal", series([0.50, 0.52, 0.60, 0.75]))

    assert analysis.windows[2].acceleration == 0.06
    assert analysis.windows[3].acceleration == 0.07
    assert analysis.trend_state == "accelerating"


def test_stable_series_has_no_change_point():
    analysis = analyze_trend_series(
        "s", "market_signal", series([0.40, 0.41, 0.39, 0.40, 0.41])
    )

    assert analysis.trend_state == "stable"
    assert analysis.change_points == ()


def test_rising_state():
    analysis = analyze_trend_series(
        "s", "market_signal", series([0.20, 0.25, 0.30, 0.35, 0.40])
    )

    assert analysis.trend_state == "rising"
    assert analysis.change_points == ()
    assert analysis.state_confidence > 0
    assert analysis.change_point_confidence is None
    assert analysis.confidence == analysis.state_confidence


def test_declining_state():
    analysis = analyze_trend_series(
        "s", "market_signal", series([0.70, 0.68, 0.65, 0.45, 0.30])
    )

    assert analysis.trend_state == "declining"


def test_volatile_state():
    analysis = analyze_trend_series(
        "s", "market_signal", series([0.20, 0.60, 0.25, 0.65, 0.22])
    )

    assert analysis.trend_state == "volatile"
    assert analysis.change_points == ()


def test_sudden_rise_detects_change_point():
    analysis = analyze_trend_series(
        "s", "market_signal", series([0.20, 0.21, 0.19, 0.45, 0.60, 0.68])
    )

    assert len(analysis.change_points) == 1
    change = analysis.change_points[0]
    assert change.change_point_window == "w4"
    assert change.direction == "rising"
    assert change.before_mean == 0.2
    assert change.after_mean == 0.576667
    assert change.growth_rate == 0.26


def test_decline_detects_change_point():
    analysis = analyze_trend_series(
        "s", "market_signal", series([0.70, 0.68, 0.65, 0.45, 0.30])
    )

    assert len(analysis.change_points) == 1
    change = analysis.change_points[0]
    assert change.change_point_window == "w4"
    assert change.direction == "declining"
    assert change.before_mean == 0.676667
    assert change.after_mean == 0.375


def test_single_spike_is_not_persistent_change_point():
    analysis = analyze_trend_series(
        "s", "market_signal", series([0.30, 0.31, 0.29, 0.60, 0.30, 0.31])
    )

    assert analysis.change_points == ()
    assert analysis.trend_state == "stable"


def test_persistence_threshold_prevents_single_window_change():
    config = dict(DEFAULT_TREND_CHANGE_CONFIG)
    config["min_persistence_windows"] = 3
    analysis = analyze_trend_series(
        "s",
        "market_signal",
        series([0.20, 0.21, 0.19, 0.45, 0.60, 0.68]),
        config=config,
    )

    assert analysis.change_points == ()


def test_window_stability_is_deterministic_and_directional():
    stable = analyze_trend_series(
        "s", "market_signal", series([0.40, 0.41, 0.39, 0.40, 0.41])
    )
    spike = analyze_trend_series(
        "s", "market_signal", series([0.30, 0.31, 0.29, 0.60, 0.30, 0.31])
    )

    assert stable.window_stability == 0.75
    assert stable.window_stability > spike.window_stability
    assert analyze_trend_series(
        "s", "market_signal", series([0.40, 0.41, 0.39, 0.40, 0.41])
    ).window_stability == stable.window_stability


def test_change_confidence_is_deterministic_and_bounded():
    first = analyze_trend_series(
        "s", "market_signal", series([0.20, 0.21, 0.19, 0.45, 0.60, 0.68])
    )
    second = analyze_trend_series(
        "s", "market_signal", series([0.20, 0.21, 0.19, 0.45, 0.60, 0.68])
    )

    assert first.change_points[0].confidence == second.change_points[0].confidence
    assert 0.0 <= first.change_points[0].confidence <= 1.0
    assert first.change_point_confidence == first.change_points[-1].confidence
    assert first.confidence == first.state_confidence
    assert first.state_confidence != first.change_point_confidence


def test_change_point_evidence_and_lineage_preserved():
    windows = [
        TrendWindowScore(
            "s",
            "market_signal",
            f"w{index + 1}",
            value,
            source_records=(f"record-{index + 1}",),
            evidence_ids=(f"evidence-{index + 1}",),
            trend_report_id="report-1" if index == 3 else None,
            analysis_run_id="run-1",
        )
        for index, value in enumerate([0.20, 0.21, 0.19, 0.45, 0.60, 0.68])
    ]
    analysis = analyze_trend_series(
        "s",
        "market_signal",
        windows,
        algorithm_version="trend-change.v1",
    )

    change = analysis.change_points[0]
    assert change.evidence["baseline_windows"] == ["w1", "w2", "w3"]
    assert change.evidence["persistent_windows"] == ["w4", "w5"]
    assert change.lineage["source_records"] == [
        "record-1",
        "record-2",
        "record-3",
        "record-4",
        "record-5",
        "record-6",
    ]
    assert change.lineage["trend_report_ids"] == ["report-1"]
    assert change.lineage["analysis_run_ids"] == ["run-1"]
    assert change.lineage["algorithm_version"] == "trend-change.v1"
    assert len(change.lineage["input_scores"]) == 6


def test_same_input_produces_identical_analysis():
    windows = series([0.20, 0.21, 0.19, 0.45, 0.60, 0.68])

    first = asdict(analyze_trend_series("s", "market_signal", windows))
    second = asdict(analyze_trend_series("s", "market_signal", windows))

    assert first == second


class FakeTrendChangeStore:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, object]] = {}

    def create(self, payload: dict[str, object]) -> dict[str, object]:
        analysis_id = f"analysis-{len(self.records) + 1}"
        record = {"analysis_id": analysis_id, "created_at": "2026-08-07T00:00:00Z", **payload}
        self.records[analysis_id] = record
        return record

    def get(self, analysis_id: str) -> dict[str, object] | None:
        return self.records.get(analysis_id)


def test_trend_change_service_filters_subjects_windows_and_change_points():
    service = TrendChangeService(FakeTrendChangeStore())
    request = {
        "request_id": "request-1",
        "subjects": [
            {
                "subject_id": "subject-rising",
                "subject_type": "market_signal",
                "windows": [
                    {"window": f"w{index + 1}", "score": score}
                    for index, score in enumerate([0.20, 0.21, 0.19, 0.45, 0.60, 0.68])
                ],
            },
            {
                "subject_id": "subject-stable",
                "subject_type": "market_signal",
                "windows": [
                    {"window": f"w{index + 1}", "score": score}
                    for index, score in enumerate([0.40, 0.41, 0.39, 0.40, 0.41])
                ],
            },
        ],
    }
    created = service.analyze(request)
    analysis_id = str(created["analysis_id"])

    filtered = service.get(analysis_id, subject_id="subject-rising")
    assert [subject["subject_id"] for subject in filtered["subjects"]] == [
        "subject-rising"
    ]
    assert filtered["subjects"][0]["trend_state"] == "rising"
    assert filtered["subjects"][0]["state_confidence"] > 0
    assert (
        filtered["subjects"][0]["confidence"]
        == filtered["subjects"][0]["state_confidence"]
    )

    by_window = service.get(analysis_id, subject_id="subject-rising", window="w4")
    assert [item["window"] for item in by_window["subjects"][0]["windows"]] == ["w4"]

    stable = service.get(analysis_id, trend_state="stable")
    assert [subject["subject_id"] for subject in stable["subjects"]] == ["subject-stable"]

    points = service.change_points(analysis_id, trend_state="rising")
    assert [point["change_point_window"] for point in points] == ["w4"]
    assert service.change_points(analysis_id, trend_state="stable") == []


def test_algorithm_version_is_recorded():
    analysis = analyze_trend_series(
        "s",
        "market_signal",
        series([0.20, 0.21, 0.19, 0.45, 0.60, 0.68]),
        algorithm_version=DEFAULT_ALGORITHM_VERSION,
    )

    assert analysis.algorithm_version == DEFAULT_ALGORITHM_VERSION
    assert analysis.lineage["method"] == "rolling_baseline_zscore"


def test_low_absolute_support_marks_insufficient_evidence():
    """低量噪声主题（absolute_support < 阈值）不产出强趋势结论，也不保留 CP。"""
    windows = series([0.31, 0.85, 1.0, 0.92, 0.54, 0.69, 0.54], absolute_support=0.01)
    analysis = analyze_trend_series("s", "market_signal", windows)

    assert analysis.trend_state == "insufficient_evidence"
    assert analysis.change_points == ()


def test_default_absolute_support_does_not_trigger_gate():
    """默认 absolute_support=1.0 不触发门禁，declining 序列正常判定。"""
    analysis = analyze_trend_series(
        "s", "market_signal", series([0.70, 0.68, 0.65, 0.45, 0.30])
    )

    assert analysis.trend_state == "declining"
    assert len(analysis.change_points) == 1


def test_high_absolute_support_still_classifies_normally():
    """absolute_support >= 阈值时正常判定。"""
    windows = series([0.99, 1.0, 0.90, 0.83, 0.82, 0.73, 0.59], absolute_support=0.9)
    analysis = analyze_trend_series("s", "market_signal", windows)

    assert analysis.trend_state == "declining"


def test_gradual_peak_decline_detects_turning_point():
    """缓降趋势（z-score 每步 drop < min_abs_change）由方向反转拐点补出 CP。"""
    analysis = analyze_trend_series(
        "s", "market_signal", series([0.994, 1.0, 0.899, 0.834, 0.822, 0.729, 0.595])
    )

    assert analysis.trend_state == "declining"
    assert [c.change_point_window for c in analysis.change_points] == ["w2"]
    assert analysis.change_points[0].direction == "declining"


def test_gradual_reversal_tolerates_one_small_rebound():
    analysis = analyze_trend_series(
        "s", "market_signal", series([0.85, 1.0, 0.90, 0.92, 0.82, 0.74])
    )

    assert [point.change_point_window for point in analysis.change_points] == ["w2"]
    assert analysis.change_points[0].direction == "declining"


def test_directionally_inconsistent_noise_is_not_a_turning_point():
    analysis = analyze_trend_series(
        "s", "market_signal", series([0.85, 1.0, 0.90, 0.94, 0.86, 0.92, 0.84])
    )

    assert analysis.change_points == ()


def test_gradual_trough_rise_detects_turning_point():
    """缓升趋势由方向反转拐点补出 rising CP。"""
    analysis = analyze_trend_series(
        "s", "market_signal", series([0.60, 0.595, 0.61, 0.62, 0.64, 0.68, 0.75])
    )

    assert [c.change_point_window for c in analysis.change_points] == ["w2"]
    assert analysis.change_points[0].direction == "rising"


def test_turning_point_does_not_duplicate_abrupt_zscore_change():
    """突变型序列仍由 z-score 报 CP，方向反转拐点不重复补。"""
    analysis = analyze_trend_series(
        "s", "market_signal", series([0.20, 0.21, 0.19, 0.45, 0.60, 0.68])
    )

    assert len(analysis.change_points) == 1
    assert analysis.change_points[0].change_point_window == "w4"


def test_zero_to_positive_is_newly_observed_without_fake_relative_growth():
    """0 -> positive：relative_growth 必须为 None，且显式标记 newly_observed。"""
    analysis = analyze_trend_series(
        "s", "market_signal", series([0.0, 0.5, 0.6])
    )

    assert analysis.windows[1].relative_growth is None
    assert analysis.windows[1].baseline_status == "newly_observed"
    # absolute_growth / growth_rate 仍保留，趋势分类不丢失
    assert analysis.windows[1].absolute_growth == 0.5
    assert analysis.windows[1].growth_rate == 0.5
    assert analysis.windows[2].baseline_status == "comparable"
    assert analysis.windows[2].relative_growth == 0.2


def test_zero_to_zero_has_no_baseline_without_fake_growth():
    """0 -> 0：不得显示 +0% 或 +100%，使用 no_baseline 状态。"""
    analysis = analyze_trend_series(
        "s", "market_signal", series([0.0, 0.0, 0.5])
    )

    assert analysis.windows[1].relative_growth is None
    assert analysis.windows[1].baseline_status == "no_baseline"
    assert analysis.windows[1].absolute_growth == 0.0
    assert analysis.windows[1].growth_rate == 0.0
    assert analysis.windows[2].relative_growth is None
    assert analysis.windows[2].baseline_status == "newly_observed"


def test_first_window_has_no_baseline_status():
    analysis = analyze_trend_series(
        "s", "market_signal", series([0.1, 0.2])
    )

    assert analysis.windows[0].baseline_status is None
    assert analysis.windows[1].baseline_status == "comparable"
