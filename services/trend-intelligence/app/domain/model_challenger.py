"""A26: 时序模型 Challenger — Prophet/ETS/ARIMA vs Champion 回测。

纯领域逻辑模块，所有模型从零实现，不依赖 statsmodels/prophet 等外部库。
与 A22 的 rolling_backtest 共享回测框架，对比各模型的预测精度。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

# ═══════════════════════════════════════════════════════════════════
# 通用类型
# ═══════════════════════════════════════════════════════════════════

ChallengerName = Literal[
    "champion_regularized_trend",
    "challenger_prophet_light",
    "challenger_ets",
    "challenger_arima",
    "challenger_naive",
]

ALL_CHALLENGERS: tuple[ChallengerName, ...] = (
    "champion_regularized_trend",
    "challenger_prophet_light",
    "challenger_ets",
    "challenger_arima",
    "challenger_naive",
)


@dataclass(frozen=True)
class ModelBacktestSlice:
    """单个模型的单次回测切片。"""
    train_end_index: int
    horizon: int
    mae: float
    rmse: float
    mape: float
    forecasts: tuple[float, ...]
    actuals: tuple[float, ...]


@dataclass(frozen=True)
class ModelResult:
    """单个模型对单个 subject 的回测结果。"""
    subject_id: str
    model: ChallengerName
    slices: tuple[ModelBacktestSlice, ...]
    avg_mae: float
    avg_rmse: float
    avg_mape: float
    win_count: int = 0  # vs champion


@dataclass(frozen=True)
class ChampionComparison:
    """Champion vs Challenger 完整比较报告。"""
    subject_id: str
    champion: ModelResult
    challengers: tuple[ModelResult, ...]
    winner: ChallengerName
    champion_win_rate: float
    summary: str


# ═══════════════════════════════════════════════════════════════════
# 轻量 Prophet（Fourier 趋势 + 虚拟季节性）
# ═══════════════════════════════════════════════════════════════════

def _light_prophet_forecast(
    history: Sequence[float],
    steps: int,
    *,
    changepoint_scale: float = 0.05,
    fourier_order: int = 3,
    period: float = 7.0,
) -> list[float]:
    """轻量 Prophet 风格预测：分段线性趋势 + Fourier 周期项。

    与完整 Prophet 的关键差异：
    - 无自动 changepoint 检测（使用均匀间隔）
    - 无节假日效应
    - 无不确定性采样
    """
    n = len(history)
    if n < 2:
        return [history[-1] if history else 0.0] * steps

    x = list(range(n))
    y = list(history)

    # 趋势：带变点的分段线性
    n_changepoints = max(1, int(n * changepoint_scale))
    cp_indices = [
        int(n * (i + 1) / (n_changepoints + 1))
        for i in range(n_changepoints)
    ]

    # 构建设计矩阵 X = [1, t, (t-cp1)+, ..., (t-cpk)+, sin(...), cos(...)]
    design: list[list[float]] = []
    for t in x:
        row = [1.0, float(t)]
        for cp in cp_indices:
            row.append(max(0.0, float(t - cp)))
        # Fourier 项
        for order in range(1, fourier_order + 1):
            row.append(math.sin(2 * math.pi * order * t / period))
            row.append(math.cos(2 * math.pi * order * t / period))
        design.append(row)

    # 最小二乘拟合（正规方程求解）
    n_cols = len(design[0])
    # X^T X
    xtx = [[0.0] * n_cols for _ in range(n_cols)]
    xty = [0.0] * n_cols
    for i, row in enumerate(design):
        for j in range(n_cols):
            for k in range(n_cols):
                xtx[j][k] += row[j] * row[k]
            xty[j] += row[j] * y[i]

    # L2 正则化
    lam = 0.1
    for j in range(n_cols):
        xtx[j][j] += lam

    # 高斯消元求解
    beta = _solve_linear_system(xtx, xty)

    # 预测
    forecasts: list[float] = []
    for step in range(1, steps + 1):
        t = n + step - 1
        row = [1.0, float(t)]
        for cp in cp_indices:
            row.append(max(0.0, float(t - cp)))
        for order in range(1, fourier_order + 1):
            row.append(math.sin(2 * math.pi * order * t / period))
            row.append(math.cos(2 * math.pi * order * t / period))
        pred = sum(row[j] * beta[j] for j in range(n_cols))
        forecasts.append(round(max(0.0, pred), 6))
    return forecasts


# ═══════════════════════════════════════════════════════════════════
# 轻量 ETS（指数平滑状态空间模型）
# ═══════════════════════════════════════════════════════════════════

def _ets_forecast(
    history: Sequence[float],
    steps: int,
    *,
    alpha: float = 0.3,
    beta: float = 0.1,
    phi: float = 0.9,
) -> list[float]:
    """Holt-Winters 加性趋势（无季节性）指数平滑。

    状态方程：
      level[t]   = alpha * y[t] + (1-alpha) * (level[t-1] + phi*b[t-1])
      trend[t]   = beta * (level[t] - level[t-1]) + (1-beta) * phi*b[t-1]
    """
    n = len(history)
    if n == 0:
        return [0.0] * steps
    if n == 1:
        return [history[0]] * steps

    # 初始化
    level = history[0]
    trend = (history[-1] - history[0]) / max(n - 1, 1)

    # 拟合
    for t_val in range(1, n):
        prev_level = level
        level = alpha * history[t_val] + (1 - alpha) * (level + phi * trend)
        trend = beta * (level - prev_level) + (1 - beta) * phi * trend

    # 预测
    forecasts: list[float] = []
    for step in range(1, steps + 1):
        damped_trend = sum(phi ** k for k in range(step)) * trend
        pred = level + damped_trend
        forecasts.append(round(max(0.0, pred), 6))
    return forecasts


# ═══════════════════════════════════════════════════════════════════
# 轻量 ARIMA（AR + I + MA 分解）
# ═══════════════════════════════════════════════════════════════════

def _arima_forecast(
    history: Sequence[float],
    steps: int,
    *,
    p: int = 2,
    d: int = 1,
    q: int = 1,
) -> list[float]:
    """轻量 ARIMA(p,d,q) 预测。

    - 差分 d 次使序列平稳
    - AR(p) 自回归部分
    - MA(q) 用残差近似
    """
    n = len(history)
    if n < 2:
        return [history[-1] if history else 0.0] * steps

    # 差分
    diffed = list(history)
    for _ in range(d):
        diffed = [diffed[i] - diffed[i - 1] for i in range(1, len(diffed))]

    m = len(diffed)
    if m <= p:
        return [history[-1]] * steps

    # AR(p) 系数估计（Yule-Walker 方法简化版：最小二乘）
    ar_coeffs = _estimate_ar_coeffs(diffed, p)

    # MA(q) 残差
    residuals: list[float] = []
    for t_idx in range(p, m):
        pred = sum(ar_coeffs[j] * diffed[t_idx - 1 - j] for j in range(p))
        residuals.append(diffed[t_idx] - pred)
    ma_term = sum(residuals[-q:]) / max(len(residuals[-q:]), 1) if q > 0 else 0.0

    # 预测差分值
    forecast_diffed: list[float] = []
    working = list(diffed)
    for _ in range(steps):
        if len(working) >= p:
            pred = sum(
                ar_coeffs[j] * working[-1 - j] for j in range(p)
            ) + ma_term
        else:
            pred = working[-1]
        forecast_diffed.append(pred)
        working.append(pred)

    # 逆差分还原
    last_original = history[-1]
    last_diffed_values = list(history[-d:]) if d > 0 else [last_original]
    forecasts: list[float] = []
    for fd in forecast_diffed:
        current = fd
        for prev in reversed(last_diffed_values):
            current = prev + current
        forecasts.append(round(max(0.0, current), 6))
        if d > 0:
            last_diffed_values = last_diffed_values[1:] + [current]
    return forecasts


def _estimate_ar_coeffs(
    series: Sequence[float], p: int,
) -> list[float]:
    """最小二乘估计 AR(p) 系数。"""
    n = len(series)
    if n <= p:
        return [0.0] * p

    # 构建设计矩阵和响应向量
    y_vec = [series[t] for t in range(p, n)]
    X = [
        [series[t - 1 - j] for j in range(p)]
        for t in range(p, n)
    ]

    n_rows = len(y_vec)
    # X^T X (p × p)
    xtx = [[0.0] * p for _ in range(p)]
    xty = [0.0] * p
    for i in range(n_rows):
        for j in range(p):
            for k in range(p):
                xtx[j][k] += X[i][j] * X[i][k]
            xty[j] += X[i][j] * y_vec[i]

    # 正则化
    lam = 0.01
    for j in range(p):
        xtx[j][j] += lam

    return _solve_linear_system(xtx, xty)


def _naive_forecast(history: Sequence[float], steps: int) -> list[float]:
    """Naive 预测：最后一个值重复。"""
    if not history:
        return [0.0] * steps
    return [history[-1]] * steps


# ═══════════════════════════════════════════════════════════════════
# 线性方程组求解
# ═══════════════════════════════════════════════════════════════════

def _solve_linear_system(A: list[list[float]], b: list[float]) -> list[float]:
    """高斯消元求解 Ax = b（带部分选主元）。"""
    n = len(b)
    # 增广矩阵
    aug = [row[:] + [b[i]] for i, row in enumerate(A)]

    for col in range(n):
        # 选主元
        max_row = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[max_row][col]) < 1e-12:
            continue
        aug[col], aug[max_row] = aug[max_row], aug[col]

        pivot = aug[col][col]
        for j in range(col, n + 1):
            aug[col][j] /= pivot

        for row in range(n):
            if row != col:
                factor = aug[row][col]
                for j in range(col, n + 1):
                    aug[row][j] -= factor * aug[col][j]

    return [aug[i][n] for i in range(n)]


# ═══════════════════════════════════════════════════════════════════
# Champion vs Challenger 回测框架
# ═══════════════════════════════════════════════════════════════════

FORECAST_FUNCTIONS = {
    "champion_regularized_trend": None,  # 需要从 trend_robustness 导入
    "challenger_prophet_light": _light_prophet_forecast,
    "challenger_ets": _ets_forecast,
    "challenger_arima": _arima_forecast,
    "challenger_naive": _naive_forecast,
}


def backtest_model(
    values: Sequence[float],
    *,
    subject_id: str,
    model: ChallengerName,
    min_train_size: int = 3,
    max_horizon: int = 4,
    step_size: int = 1,
    champion_fn=None,
    **params,
) -> ModelResult:
    """对单个模型执行滚动回测。"""
    n = len(values)
    forecast_fn = (
        champion_fn if model == "champion_regularized_trend"
        else FORECAST_FUNCTIONS[model]
    )

    slices: list[ModelBacktestSlice] = []
    for train_end in range(min_train_size, n, step_size):
        train = values[:train_end]
        test_start = train_end
        horizon = min(max_horizon, n - test_start)
        if horizon == 0:
            break

        test = values[test_start:test_start + horizon]
        preds = forecast_fn(train, horizon, **params)

        errors = [abs(p - a) for p, a in zip(preds, test)]
        sq_errors = [(p - a) ** 2 for p, a in zip(preds, test)]
        mae = sum(errors) / len(errors)
        rmse = math.sqrt(sum(sq_errors) / len(sq_errors))
        mape_vals = [
            abs(p - a) / max(abs(a), 1e-6)
            for p, a in zip(preds, test)
        ]
        mape = sum(mape_vals) / len(mape_vals)

        slices.append(
            ModelBacktestSlice(
                train_end_index=train_end,
                horizon=horizon,
                mae=round(mae, 6),
                rmse=round(rmse, 6),
                mape=round(mape, 4),
                forecasts=tuple(preds),
                actuals=tuple(test),
            )
        )

    if slices:
        avg_mae = round(sum(s.mae for s in slices) / len(slices), 6)
        avg_rmse = round(
            math.sqrt(sum(s.rmse ** 2 for s in slices) / len(slices)), 6
        )
        avg_mape = round(sum(s.mape for s in slices) / len(slices), 4)
    else:
        avg_mae = 0.0
        avg_rmse = 0.0
        avg_mape = 0.0

    return ModelResult(
        subject_id=subject_id,
        model=model,
        slices=tuple(slices),
        avg_mae=avg_mae,
        avg_rmse=avg_rmse,
        avg_mape=avg_mape,
    )


@dataclass(frozen=True)
class ModelCompetitionResult:
    """完整模型竞赛结果（多个 subject）。"""
    subjects: tuple[str, ...]
    models: tuple[ChallengerName, ...]
    results: tuple[ModelResult, ...]
    champion: ChallengerName
    champion_win_count: int
    total_comparisons: int
    best_challenger: ChallengerName


def run_model_competition(
    subjects_data: Sequence[tuple[str, Sequence[float]]],
    *,
    min_train_size: int = 3,
    max_horizon: int = 4,
    champion_fn=None,
) -> ModelCompetitionResult:
    """在多个 subject 上运行完整模型竞赛。

    subjects_data: [(subject_id, values), ...]
    返回聚合竞赛结果。
    """
    all_results: list[ModelResult] = []
    win_counts: dict[str, int] = {m: 0 for m in ALL_CHALLENGERS}
    total_comps = 0

    for subject_id, values in subjects_data:
        if len(values) < min_train_size + 1:
            continue

        model_results: dict[str, ModelResult] = {}
        for model in ALL_CHALLENGERS:
            params: dict = {}
            if model == "challenger_prophet_light":
                params = {"fourier_order": min(3, len(values) // 4 + 1)}
            elif model == "challenger_arima":
                params = {"p": min(2, len(values) // 3), "d": 1, "q": 1}
            elif model == "challenger_ets":
                params = {"alpha": 0.3, "beta": 0.1, "phi": 0.9}

            result = backtest_model(
                values,
                subject_id=subject_id,
                model=model,
                min_train_size=min_train_size,
                max_horizon=max_horizon,
                champion_fn=champion_fn,
                **params,
            )
            model_results[model] = result
            all_results.append(result)

        # 找出该 subject 的赢家（最低 MAE）
        best_model = min(
            model_results,
            key=lambda m: model_results[m].avg_mae,
        )
        win_counts[best_model] += 1
        total_comps += 1

    # 找出综合赢家
    best_challenger = max(
        [m for m in ALL_CHALLENGERS if m != "champion_regularized_trend"],
        key=lambda m: win_counts.get(m, 0),
    )

    return ModelCompetitionResult(
        subjects=tuple(s[0] for s in subjects_data),
        models=ALL_CHALLENGERS,
        results=tuple(all_results),
        champion="champion_regularized_trend",
        champion_win_count=win_counts.get("champion_regularized_trend", 0),
        total_comparisons=total_comps,
        best_challenger=best_challenger,
    )


def compare_champion_vs_single_challenger(
    values: Sequence[float],
    *,
    subject_id: str,
    challenger: ChallengerName = "challenger_prophet_light",
    min_train_size: int = 3,
    max_horizon: int = 4,
    champion_fn=None,
    **challenger_params,
) -> ChampionComparison:
    """Champion vs 单个 Challenger 的详细比较。"""
    champion_result = backtest_model(
        values,
        subject_id=subject_id,
        model="champion_regularized_trend",
        min_train_size=min_train_size,
        max_horizon=max_horizon,
        champion_fn=champion_fn,
    )
    challenger_result = backtest_model(
        values,
        subject_id=subject_id,
        model=challenger,
        min_train_size=min_train_size,
        max_horizon=max_horizon,
        **challenger_params,
    )

    # 逐切片比较谁赢
    champion_wins = 0
    total_slices = min(len(champion_result.slices), len(challenger_result.slices))
    for i in range(total_slices):
        if champion_result.slices[i].mae <= challenger_result.slices[i].mae:
            champion_wins += 1

    champion_win_rate = round(
        champion_wins / max(total_slices, 1), 4
    )

    if champion_result.avg_mae <= challenger_result.avg_mae:
        winner: ChallengerName = "champion_regularized_trend"
        summary = (
            f"Champion 胜出（MAE {champion_result.avg_mae} vs "
            f"{challenger} {challenger_result.avg_mae}），"
            f"切片胜率 {champion_win_rate:.1%}"
        )
    else:
        winner = challenger
        summary = (
            f"{challenger} 胜出（MAE {challenger_result.avg_mae} vs "
            f"Champion {champion_result.avg_mae}），"
            f"Champion 切片胜率 {champion_win_rate:.1%}"
        )

    return ChampionComparison(
        subject_id=subject_id,
        champion=champion_result,
        challengers=(challenger_result,),
        winner=winner,
        champion_win_rate=champion_win_rate,
        summary=summary,
    )
