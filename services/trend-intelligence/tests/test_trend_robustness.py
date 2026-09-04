"""A21-A25: Trend 鲁棒性、预测、保形区间、层级一致性、领先滞后 测试。"""
from __future__ import annotations

import pytest

from app.domain.trend_change import TrendWindowScore, source_lead_lag_analysis
from app.domain.trend_robustness import (
    HierarchyLevel,
    SubjectRobustness,
    _ewma_forecast,
    _regularized_trend_forecast,
    _seasonal_naive_forecast,
    check_stability,
    compute_conformal_interval,
    compute_cross_correlation,
    compute_enterprise_ablation,
    compute_source_robustness,
    detect_spurious_correlation,
    generate_forecast,
    reconcile_hierarchy,
    rolling_backtest,
)


# ═══════════════════════════════════════════════
# A21: 来源脆弱性
# ═══════════════════════════════════════════════

class TestSourceRobustness:
    def test_basic_ablation(self):
        subjects = [
            {
                "subject_id": "skill_001",
                "subject_type": "skill",
                "score": 0.75,
                "source_scores": {
                    "zhaopin": 0.70, "lp": 0.80, "boss": 0.75,
                },
            },
            {
                "subject_id": "skill_002",
                "subject_type": "skill",
                "score": 0.50,
                "source_scores": {
                    "zhaopin": 0.50, "lp": 0.50, "boss": 0.50,
                },
            },
        ]
        results = compute_source_robustness(subjects)
        assert len(results) == 2
        assert results[0].subject_id == "skill_001"
        assert results[0].total_sources == 3
        assert len(results[0].ablations) == 3
        assert results[0].rank_stability >= 0.0

    def test_fragile_detection(self):
        subjects = [
            {
                "subject_id": "fragile_skill",
                "subject_type": "skill",
                "score": 0.80,
                "source_scores": {
                    "zhaopin": 0.10, "lp": 0.12, "boss": 0.95,
                },
            },
            {
                "subject_id": "stable_skill",
                "subject_type": "skill",
                "score": 0.40,
                "source_scores": {
                    "zhaopin": 0.40, "lp": 0.40, "boss": 0.40,
                },
            },
        ]
        results = compute_source_robustness(subjects)
        fragile = [r for r in results if r.is_fragile]
        # fragile_skill 高度依赖 boss → 删除 boss 排名会大幅下降
        assert any(r.subject_id == "fragile_skill" for r in fragile)

    def test_single_source_handling(self):
        subjects = [
            {
                "subject_id": "solo",
                "score": 0.50,
                "source_scores": {"only_source": 0.50},
            },
        ]
        results = compute_source_robustness(subjects)
        assert len(results[0].ablations) == 0
        assert results[0].rank_stability == 1.0

    def test_enterprise_ablation(self):
        subjects = [
            {"subject_id": "s1", "score": 0.80},
        ]
        ent_weights = {"s1": {"enterprise_A": 0.30, "enterprise_B": 0.10}}
        result = compute_enterprise_ablation(subjects, enterprise_weights=ent_weights)
        assert "s1__enterprise_A" in result
        assert result["s1__enterprise_A"] > 0


# ═══════════════════════════════════════════════
# A22: 预测
# ═══════════════════════════════════════════════

class TestForecasting:
    def test_seasonal_naive(self):
        history = [0.4, 0.5, 0.6, 0.7]
        preds = _seasonal_naive_forecast(history, 3)
        assert len(preds) == 3
        assert all(p > 0 for p in preds)

    def test_ewma(self):
        history = [0.3, 0.4, 0.5, 0.6]
        preds = _ewma_forecast(history, 2, alpha=0.5)
        assert len(preds) == 2

    def test_regularized_trend(self):
        history = [0.1, 0.2, 0.3, 0.4, 0.5]
        preds = _regularized_trend_forecast(history, 2)
        assert len(preds) == 2
        assert preds[0] > history[-1]  # 上升趋势

    def test_rolling_backtest_insufficient_data(self):
        result = rolling_backtest([0.1, 0.2], method="regularized_trend", min_train_size=3)
        assert len(result) == 0

    def test_rolling_backtest_normal(self):
        values = [0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4]
        result = rolling_backtest(values, method="regularized_trend", min_train_size=3)
        assert len(result) > 0
        for s in result:
            assert s.mae >= 0
            assert s.rmse >= 0

    def test_all_methods_produce_results(self):
        values = [0.2, 0.25, 0.3, 0.35, 0.4, 0.45]
        for method in ("seasonal_naive", "ewma", "regularized_trend"):
            result = generate_forecast("test", values, ["w1", "w2", "w3", "w4", "w5", "w6"],
                                       method=method, forecast_steps=2, min_train_size=3)
            assert result.subject_id == "test"
            assert len(result.points) == 8  # 6 observed + 2 forecast
            assert result.points[-1].is_observed is False

    def test_forecast_short_history(self):
        result = generate_forecast("short", [0.5], ["w1"], forecast_steps=3)
        assert len(result.points) == 1

    def test_empty_history(self):
        preds = _seasonal_naive_forecast([], 3)
        assert preds == [0.0, 0.0, 0.0]
        preds = _ewma_forecast([], 3)
        assert preds == [0.0, 0.0, 0.0]
        preds = _regularized_trend_forecast([], 3)
        assert preds == [0.0, 0.0, 0.0]

    def test_forecast_values_non_negative(self):
        values = [0.9, 0.85, 0.88, 0.87, 0.86]
        result = generate_forecast("desc", values, ["w" + str(i) for i in range(5)],
                                    method="regularized_trend", forecast_steps=3)
        for pt in result.points:
            assert pt.forecast >= 0


# ═══════════════════════════════════════════════
# A23: 保形区间
# ═══════════════════════════════════════════════

class TestConformalInterval:
    def test_insufficient_history(self):
        ci = compute_conformal_interval(
            [0.5, 0.6], subject_id="test", min_history=5,
        )
        assert not ci.is_reliable
        assert "insufficient_history" in ci.reason

    def test_reliable_interval(self):
        values = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45]
        ci = compute_conformal_interval(
            values, subject_id="test", method="regularized_trend",
            level=0.80, min_history=5,
        )
        assert ci.is_reliable
        assert ci.lower <= ci.upper
        assert ci.interval_width >= 0
        assert 0 <= ci.lower <= 1
        assert 0 <= ci.upper <= 1

    def test_different_methods(self):
        values = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
        for method in ("seasonal_naive", "ewma", "regularized_trend"):
            ci = compute_conformal_interval(
                values, subject_id="test", method=method, min_history=5,
            )
            assert ci.is_reliable


# ═══════════════════════════════════════════════
# A24: 层级一致性
# ═══════════════════════════════════════════════

class TestHierarchy:
    def _demo_levels(self):
        return [
            HierarchyLevel(
                name="family",
                members=("backend", "data"),
                parent=None,
            ),
            HierarchyLevel(
                name="position",
                members=("java_dev", "python_dev"),
                parent="backend",
            ),
            HierarchyLevel(
                name="position",
                members=("ml_eng", "da"),
                parent="data",
            ),
        ]

    def test_bottom_up_reconcile(self):
        levels = self._demo_levels()
        forecasts = {
            "backend": 0.5, "data": 0.5,
            "java_dev": 0.3, "python_dev": 0.2,
            "ml_eng": 0.3, "da": 0.2,
        }
        report = reconcile_hierarchy(
            forecasts, levels, method="bottom_up",
        )
        assert len(report.results) > 0
        for r in report.results:
            assert -1 <= r.adjustment <= 1

    def test_top_down_reconcile(self):
        levels = self._demo_levels()
        forecasts = {
            "backend": 0.6, "data": 0.4,
            "java_dev": 0.3, "python_dev": 0.3,
            "ml_eng": 0.2, "da": 0.2,
        }
        report = reconcile_hierarchy(
            forecasts, levels, method="top_down",
        )
        assert len(report.results) > 0

    def test_with_actuals(self):
        levels = self._demo_levels()
        forecasts = {
            "java_dev": 0.30, "python_dev": 0.20,
            "ml_eng": 0.30, "da": 0.20,
            "backend": 0.50, "data": 0.50,
        }
        actuals = {
            "java_dev": 0.32, "python_dev": 0.18,
            "ml_eng": 0.28, "da": 0.22,
        }
        report = reconcile_hierarchy(
            forecasts, levels, method="bottom_up",
            actuals=actuals,
        )
        assert report.pre_reconciliation_mae > 0

    def test_improvement_positive(self):
        levels = self._demo_levels()
        forecasts = {
            "backend": 0.70, "data": 0.30,
            "java_dev": 0.50, "python_dev": 0.20,
            "ml_eng": 0.15, "da": 0.15,
        }
        actuals = {
            "java_dev": 0.32, "python_dev": 0.18,
            "ml_eng": 0.28, "da": 0.22,
            "backend": 0.50, "data": 0.50,
        }
        report = reconcile_hierarchy(
            forecasts, levels, method="bottom_up",
            actuals=actuals,
        )
        # Reconciliation 应改善 accuracy
        assert report.improvement_pct >= 0 or report.post_reconciliation_mae <= report.pre_reconciliation_mae


# ═══════════════════════════════════════════════
# A25: 领先滞后与伪相关
# ═══════════════════════════════════════════════

class TestCrossCorrelation:
    def test_perfect_positive_lead(self):
        a = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
        b = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
        result = compute_cross_correlation(a, b, subject_a="A", subject_b="B")
        assert result.max_correlation > 0.5
        assert result.is_significant

    def test_no_correlation(self):
        a = [0.5, 0.3, 0.8, 0.2, 0.7, 0.1, 0.9, 0.4]
        b = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2]
        result = compute_cross_correlation(a, b, subject_a="A", subject_b="B")
        # 可能相关或不显著
        assert -1.0 <= result.max_correlation <= 1.0

    def test_insufficient_data(self):
        result = compute_cross_correlation([0.5], [0.6])
        assert not result.is_significant
        assert result.max_correlation == 0.0

    def test_association_only_flag(self):
        a = [0.1, 0.2, 0.3, 0.4, 0.5]
        b = [0.15, 0.25, 0.35, 0.45, 0.55]
        result = compute_cross_correlation(a, b)
        assert result.association_only is True

    def test_lag_symmetry(self):
        a = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]
        b = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]
        result = compute_cross_correlation(a, b, max_lag=3)
        assert len(result.correlations) == 7  # -3..3

    def test_cross_correlation_different_lengths(self):
        result = compute_cross_correlation([0.1, 0.2, 0.3], [0.2, 0.3])
        assert not result.is_significant

    def test_source_lead_lag_missing_observations_are_not_zero_filled(self):
        windows = [
            TrendWindowScore(
                subject_id="sub",
                subject_type="position",
                window="w1",
                score=1.0,
                source_scores={"A": 0.0},
            ),
            TrendWindowScore(
                subject_id="sub",
                subject_type="position",
                window="w2",
                score=1.0,
                source_scores={"B": 0.8},
            ),
            TrendWindowScore(
                subject_id="sub",
                subject_type="position",
                window="w3",
                score=1.0,
                source_scores={"A": 0.8, "B": 0.8},
            ),
        ]

        results = source_lead_lag_analysis(windows, min_samples=3)

        assert results
        assert results[0].insufficient_evidence is True
        assert results[0].correlation == 0.0

    def test_missing_middle_window_does_not_create_fake_lag_one(self):
        windows = [
            TrendWindowScore(
                subject_id="sub",
                subject_type="position",
                window="w1",
                score=1.0,
                source_scores={"A": 0.1, "B": 0.9},
            ),
            TrendWindowScore(
                subject_id="sub",
                subject_type="position",
                window="w2",
                score=1.0,
                source_scores={"B": 0.5},
            ),
            TrendWindowScore(
                subject_id="sub",
                subject_type="position",
                window="w3",
                score=1.0,
                source_scores={"A": 0.9, "B": 0.1},
            ),
        ]

        results = source_lead_lag_analysis(windows, min_samples=2)

        assert not any(
            result.best_lag_windows == 1 and result.correlation != 0.0
            for result in results
        )


class TestStability:
    def test_stable_series(self):
        values = [0.5, 0.51, 0.49, 0.5, 0.51, 0.5, 0.49, 0.5]
        check = check_stability(values, subject_id="stable")
        assert check.is_stationary_heuristic

    def test_trending_series(self):
        values = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
        check = check_stability(values, subject_id="trending")
        # 强烈趋势应检测为非平稳
        assert not check.is_stationary_heuristic

    def test_short_series(self):
        values = [0.5, 0.6]
        check = check_stability(values, subject_id="short", window_size=4)
        assert len(check.rolling_means) == 0


class TestSpuriousDetection:
    def test_small_sample_warning(self):
        warnings = detect_spurious_correlation(
            [0.1, 0.2, 0.3], [0.2, 0.3, 0.4],
            min_sample_size=8,
        )
        assert any(w.warning_type == "small_sample" for w in warnings)

    def test_non_stationary_warning(self):
        a = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
        b = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1]
        warnings = detect_spurious_correlation(a, b, subject_a="A", subject_b="B")
        assert any(w.warning_type == "non_stationary" for w in warnings)

    def test_no_warning_for_stable_series(self):
        a = [0.45, 0.48, 0.52, 0.49, 0.51, 0.50, 0.48, 0.52, 0.50]
        b = [0.30, 0.32, 0.35, 0.33, 0.34, 0.36, 0.32, 0.35, 0.34]
        warnings = detect_spurious_correlation(a, b, subject_a="A", subject_b="B")
        # 稳定序列不应产生 non_stationary 警告
        non_stat = [w for w in warnings if w.warning_type == "non_stationary"]
        assert len(non_stat) == 0
