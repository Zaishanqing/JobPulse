"""A26: 时序模型竞赛 测试。"""
from __future__ import annotations

import math

import pytest

from app.domain.model_challenger import (
    ALL_CHALLENGERS,
    _arima_forecast,
    _estimate_ar_coeffs,
    _ets_forecast,
    _light_prophet_forecast,
    _naive_forecast,
    _solve_linear_system,
    backtest_model,
    compare_champion_vs_single_challenger,
    run_model_competition,
)
from app.domain.trend_robustness import _regularized_trend_forecast


# ═══════════════════════════════════════════════
# Prophet Light
# ═══════════════════════════════════════════════

class TestProphetLight:
    def test_basic_forecast(self):
        history = [0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4]
        preds = _light_prophet_forecast(history, 3)
        assert len(preds) == 3
        assert all(p >= 0 for p in preds)

    def test_short_history(self):
        preds = _light_prophet_forecast([0.5], 2)
        assert len(preds) == 2
        assert preds[0] == pytest.approx(0.5, abs=0.01)

    def test_empty_history(self):
        preds = _light_prophet_forecast([], 3)
        assert preds == [0.0, 0.0, 0.0]

    def test_upward_trend_capture(self):
        history = [0.1, 0.15, 0.22, 0.28, 0.35, 0.42, 0.50]
        preds = _light_prophet_forecast(history, 3)
        # 上升趋势应被捕获
        assert preds[-1] > history[-1] * 0.8

    def test_different_fourier_order(self):
        history = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7]
        p1 = _light_prophet_forecast(history, 2, fourier_order=1)
        p3 = _light_prophet_forecast(history, 2, fourier_order=3)
        assert len(p1) == 2
        assert len(p3) == 2


# ═══════════════════════════════════════════════
# ETS
# ═══════════════════════════════════════════════

class TestETS:
    def test_basic_forecast(self):
        history = [0.1, 0.2, 0.3, 0.4, 0.5]
        preds = _ets_forecast(history, 3)
        assert len(preds) == 3
        assert all(p >= 0 for p in preds)

    def test_constant_series(self):
        history = [0.5, 0.5, 0.5, 0.5, 0.5]
        preds = _ets_forecast(history, 3)
        assert all(abs(p - 0.5) < 0.05 for p in preds)

    def test_single_value(self):
        preds = _ets_forecast([0.7], 2)
        assert len(preds) == 2
        assert preds[0] == pytest.approx(0.7, abs=0.01)

    def test_damped_trend(self):
        history = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]
        preds_high_phi = _ets_forecast(history, 3, phi=0.95)
        preds_low_phi = _ets_forecast(history, 3, phi=0.5)
        # 高 phi（低阻尼）应产生更大的预测值
        assert preds_high_phi[-1] >= preds_low_phi[-1]


# ═══════════════════════════════════════════════
# ARIMA
# ═══════════════════════════════════════════════

class TestARIMA:
    def test_basic_forecast(self):
        history = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]
        preds = _arima_forecast(history, 3, p=2, d=1, q=1)
        assert len(preds) == 3
        assert all(p >= 0 for p in preds)

    def test_no_differencing(self):
        history = [0.3, 0.35, 0.4, 0.45, 0.5]
        preds = _arima_forecast(history, 2, p=2, d=0, q=0)
        assert len(preds) == 2

    def test_short_series(self):
        preds = _arima_forecast([0.5, 0.6], 2)
        assert len(preds) == 2

    def test_ar_coeff_estimation(self):
        series = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
        coeffs = _estimate_ar_coeffs(series, p=2)
        assert len(coeffs) == 2
        assert all(isinstance(c, float) for c in coeffs)

    def test_ar_coeffs_insufficient(self):
        coeffs = _estimate_ar_coeffs([0.5], p=3)
        assert coeffs == [0.0, 0.0, 0.0]


# ═══════════════════════════════════════════════
# Naive
# ═══════════════════════════════════════════════

class TestNaive:
    def test_last_value_repeat(self):
        preds = _naive_forecast([0.1, 0.2, 0.7], 3)
        assert preds == [0.7, 0.7, 0.7]

    def test_empty(self):
        assert _naive_forecast([], 3) == [0.0, 0.0, 0.0]


# ═══════════════════════════════════════════════
# 线性求解器
# ═══════════════════════════════════════════════

class TestLinearSolver:
    def test_simple_2x2(self):
        A = [[2.0, 1.0], [1.0, 3.0]]
        b = [5.0, 6.0]
        x = _solve_linear_system(A, b)
        assert len(x) == 2
        # 2x + y = 5, x + 3y = 6 → x=1.8, y=1.4
        assert abs(x[0] * 2 + x[1] - 5.0) < 0.001

    def test_identity(self):
        A = [[1.0, 0.0], [0.0, 1.0]]
        b = [3.0, 4.0]
        x = _solve_linear_system(A, b)
        assert x[0] == pytest.approx(3.0)
        assert x[1] == pytest.approx(4.0)


# ═══════════════════════════════════════════════
# 模型竞赛
# ═══════════════════════════════════════════════

class TestModelCompetition:
    def _sample_values(self):
        return [0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45]

    def test_backtest_single_model(self):
        values = self._sample_values()
        result = backtest_model(
            values,
            subject_id="test",
            model="challenger_naive",
            min_train_size=3,
        )
        assert result.subject_id == "test"
        assert result.model == "challenger_naive"
        assert len(result.slices) > 0
        assert result.avg_mae >= 0

    def test_champion_vs_naive(self):
        values = self._sample_values()
        comparison = compare_champion_vs_single_challenger(
            values,
            subject_id="test",
            challenger="challenger_naive",
            champion_fn=_regularized_trend_forecast,
            min_train_size=3,
        )
        assert comparison.champion.avg_mae >= 0
        assert comparison.challengers[0].avg_mae >= 0
        assert comparison.winner in ALL_CHALLENGERS

    def test_full_competition(self):
        subjects = [
            ("trend_up", [0.1, 0.15, 0.22, 0.28, 0.35, 0.42, 0.50]),
            ("trend_flat", [0.5, 0.51, 0.49, 0.5, 0.51, 0.5, 0.49]),
        ]
        result = run_model_competition(
            subjects,
            champion_fn=_regularized_trend_forecast,
            min_train_size=3,
        )
        assert result.total_comparisons == 2
        assert len(result.results) > 0
        assert result.champion == "champion_regularized_trend"

    def test_all_challengers_produce_results(self):
        values = [0.1, 0.15, 0.22, 0.28, 0.35, 0.42, 0.50]
        for model in ALL_CHALLENGERS:
            fn_map = {
                "champion_regularized_trend": _regularized_trend_forecast,
                "challenger_prophet_light": _light_prophet_forecast,
                "challenger_ets": _ets_forecast,
                "challenger_arima": _arima_forecast,
                "challenger_naive": _naive_forecast,
            }
            fn = fn_map[model]
            preds = fn(values, 3)
            assert len(preds) == 3
            assert all(p >= 0 for p in preds)

    def test_regularized_trend_beats_naive_on_trending(self):
        """在有明确趋势的序列上，正则化趋势应优于 naive。"""
        values = [0.1, 0.15, 0.22, 0.28, 0.35, 0.42, 0.50, 0.58]
        comparison = compare_champion_vs_single_challenger(
            values,
            subject_id="trend",
            challenger="challenger_naive",
            champion_fn=_regularized_trend_forecast,
            min_train_size=3,
        )
        # 趋势序列上 champion 应该胜出
        assert comparison.champion.avg_mae <= comparison.challengers[0].avg_mae

    def test_models_produce_different_forecasts(self):
        """不同模型应产生不同的预测值。"""
        history = [0.1, 0.15, 0.22, 0.28, 0.35, 0.42, 0.50]
        forecasts = {}
        fn_map = {
            "champion": _regularized_trend_forecast,
            "prophet": _light_prophet_forecast,
            "ets": _ets_forecast,
            "arima": _arima_forecast,
        }
        for name, fn in fn_map.items():
            forecasts[name] = fn(history, 3)

        # 至少有两组不同（不要求全部不同）
        unique = set(tuple(f) for f in forecasts.values())
        assert len(unique) > 1
