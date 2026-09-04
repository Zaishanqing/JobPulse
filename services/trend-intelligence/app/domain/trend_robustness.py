"""A21-A25: Trend 鲁棒性、预测、保形区间、层级一致性与领先滞后分析。

纯领域逻辑模块，无 IO 依赖。所有函数接受领域对象并返回结果。
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal


# ═══════════════════════════════════════════════════════════════════
# A21: 来源脆弱性与删源重算
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class SourceAblationResult:
    """删除一个来源后的重算结果。"""
    removed_source: str
    original_rank: int
    ablated_rank: int
    rank_delta: int
    original_score: float
    ablated_score: float
    score_delta: float
    available_sources_after: int


@dataclass(frozen=True)
class SubjectRobustness:
    """单个 subject 的来源脆弱性分析。"""
    subject_id: str
    subject_type: str
    baseline_rank: int
    baseline_score: float
    total_sources: int
    ablations: tuple[SourceAblationResult, ...]
    max_rank_drop: int = 0
    max_score_drop: float = 0.0
    most_critical_source: str = ""
    rank_stability: float = 1.0  # 1.0 = 完全不变化

    @property
    def is_fragile(self) -> bool:
        return self.max_rank_drop >= 2 or self.max_score_drop >= 0.15


def compute_source_robustness(
    subjects: Sequence[Mapping[str, object]],
    *,
    subject_id_key: str = "subject_id",
    score_key: str = "score",
    source_scores_key: str = "source_scores",
) -> tuple[SubjectRobustness, ...]:
    """对每个 subject 做 leave-one-source-out 重算。

    subjects: 预计算的 subject 趋势结果，每个包含 source_scores。
    返回每个 subject 的来源脆弱性分析，按最大排名跌幅降序。
    """
    results: list[SubjectRobustness] = []

    # 先建立 baseline 排名
    baseline_sorted = sorted(
        enumerate(subjects),
        key=lambda x: float(x[1].get(score_key, 0.0)),
        reverse=True,
    )
    baseline_ranks: dict[int, int] = {}
    for rank, (idx, _) in enumerate(baseline_sorted):
        baseline_ranks[idx] = rank

    for idx, subject in enumerate(subjects):
        sid = str(subject.get(subject_id_key, f"subject_{idx}"))
        stype = str(subject.get("subject_type", "unknown"))
        base_score = float(subject.get(score_key, 0.0))
        source_scores = dict(subject.get(source_scores_key, {}))
        total_sources = len(source_scores)

        if total_sources <= 1:
            results.append(
                SubjectRobustness(
                    subject_id=sid, subject_type=stype,
                    baseline_rank=baseline_ranks.get(idx, 0),
                    baseline_score=base_score,
                    total_sources=total_sources,
                    ablations=(),
                    rank_stability=1.0,
                )
            )
            continue

        ablations: list[SourceAblationResult] = []
        max_rank_drop = 0
        max_score_drop = 0.0
        most_critical = ""

        for removed_source in sorted(source_scores):
            # 重算：移除该来源的分数贡献
            remaining = {
                src: score
                for src, score in source_scores.items()
                if src != removed_source
            }
            if not remaining:
                ablated_score = 0.0
            else:
                ablated_score = float(
                    sum(remaining.values()) / len(remaining)
                )

            # 重排：计算 removed 后在所有 subject 中的新排名
            new_scores: list[tuple[int, float]] = []
            for j, other in enumerate(subjects):
                if j == idx:
                    new_scores.append((j, ablated_score))
                else:
                    other_src = dict(other.get(source_scores_key, {}))
                    other_rem = {
                        s: sc for s, sc in other_src.items()
                        if s != removed_source
                    }
                    if other_rem:
                        new_scores.append(
                            (j, float(sum(other_rem.values()) / len(other_rem)))
                        )
                    else:
                        new_scores.append((j, float(other.get(score_key, 0.0))))

            new_sorted = sorted(new_scores, key=lambda x: x[1], reverse=True)
            new_rank = next(
                r for r, (j, _) in enumerate(new_sorted) if j == idx
            )

            rank_delta = baseline_ranks.get(idx, 0) - new_rank
            score_delta = base_score - ablated_score

            ablations.append(
                SourceAblationResult(
                    removed_source=removed_source,
                    original_rank=baseline_ranks.get(idx, 0),
                    ablated_rank=new_rank,
                    rank_delta=rank_delta,
                    original_score=base_score,
                    ablated_score=round(ablated_score, 4),
                    score_delta=round(score_delta, 4),
                    available_sources_after=len(remaining),
                )
            )

            if rank_delta > max_rank_drop:
                max_rank_drop = rank_delta
                most_critical = removed_source
            if score_delta > max_score_drop:
                max_score_drop = score_delta

        # rank_stability: 排名不变化的消融比例
        stable_count = sum(1 for a in ablations if a.rank_delta == 0)
        rank_stability = round(stable_count / len(ablations), 4) if ablations else 1.0

        results.append(
            SubjectRobustness(
                subject_id=sid,
                subject_type=stype,
                baseline_rank=baseline_ranks.get(idx, 0),
                baseline_score=base_score,
                total_sources=total_sources,
                ablations=tuple(ablations),
                max_rank_drop=max_rank_drop,
                max_score_drop=round(max_score_drop, 4),
                most_critical_source=most_critical,
                rank_stability=rank_stability,
            )
        )

    return tuple(results)


def compute_enterprise_ablation(
    subjects: Sequence[Mapping[str, object]],
    *,
    enterprise_weights: Mapping[str, Mapping[str, float]],
    subject_id_key: str = "subject_id",
    score_key: str = "score",
) -> dict[str, float]:
    """删除某企业贡献后重算分数变化率（可选增强）。"""
    results: dict[str, float] = {}
    for subject in subjects:
        sid = str(subject.get(subject_id_key, ""))
        base = float(subject.get(score_key, 0.0))
        ent_weights = enterprise_weights.get(sid, {})
        for ent, weight in ent_weights.items():
            new_score = max(0.0, base - weight)
            delta = (base - new_score) / max(base, 0.001)
            results[f"{sid}__{ent}"] = round(delta, 4)
    return results


# ═══════════════════════════════════════════════════════════════════
# A22: 轻量概率预测 Champion
# ═══════════════════════════════════════════════════════════════════

MethodName = Literal["seasonal_naive", "ewma", "regularized_trend"]

FORECAST_METHODS: tuple[MethodName, ...] = (
    "seasonal_naive", "ewma", "regularized_trend",
)


@dataclass(frozen=True)
class ForecastPoint:
    """单个预测点。"""
    window: str
    observed: float | None  # None 表示纯预测
    forecast: float
    lower_bound: float | None = None
    upper_bound: float | None = None
    is_observed: bool = True


@dataclass(frozen=True)
class BacktestSlice:
    """单次回测切片的指标。"""
    train_end_index: int
    test_start_index: int
    horizon: int  # 预测步数
    mae: float
    rmse: float
    mape: float
    forecasts: tuple[float, ...]
    actuals: tuple[float, ...]


@dataclass(frozen=True)
class ForecastResult:
    """一个 subject 的完整预测结果。"""
    subject_id: str
    method: MethodName
    observed_windows: tuple[str, ...]
    observed_values: tuple[float, ...]
    points: tuple[ForecastPoint, ...]
    backtest: tuple[BacktestSlice, ...]
    rolling_mae: float = 0.0
    rolling_rmse: float = 0.0
    best_params: Mapping[str, float] = field(default_factory=dict)


def _seasonal_naive_forecast(
    history: Sequence[float], steps: int, period: int = 4,
) -> list[float]:
    """季节性朴素预测：取最近 period 个值的均值外推。"""
    if not history:
        return [0.0] * steps
    recent = history[-period:] if len(history) >= period else history
    level = sum(recent) / len(recent)
    return [level] * steps


def _ewma_forecast(
    history: Sequence[float], steps: int, alpha: float = 0.3,
) -> list[float]:
    """EWMA 指数加权移动平均预测。"""
    if not history:
        return [0.0] * steps
    value = history[0]
    for h in history[1:]:
        value = alpha * h + (1 - alpha) * value
    return [value] * steps


def _regularized_trend_forecast(
    history: Sequence[float], steps: int, lambda_reg: float = 0.1,
) -> list[float]:
    """正则化线性趋势预测：岭回归拟合趋势线。"""
    n = len(history)
    if n < 2:
        return [history[-1] if history else 0.0] * steps

    # 岭回归 y = a + b*x（x 已中心化，故截距 a = y_mean）
    x_mean = (n - 1) / 2.0
    y_mean = sum(history) / n
    x = [i - x_mean for i in range(n)]
    y = [h - y_mean for h in history]

    numerator = sum(x[i] * y[i] for i in range(n))
    denominator = sum(x[i] * x[i] for i in range(n)) + lambda_reg
    slope = numerator / max(denominator, 1e-9)
    intercept = y_mean

    forecasts: list[float] = []
    for step in range(1, steps + 1):
        forecasts.append(round(max(0.0, intercept + slope * (n + step - 1 - x_mean)), 6))
    return forecasts


def rolling_backtest(
    values: Sequence[float],
    *,
    method: MethodName,
    min_train_size: int = 3,
    max_horizon: int = 4,
    step_size: int = 1,
    **params,
) -> tuple[BacktestSlice, ...]:
    """滚动窗口回测：从 min_train_size 开始逐步扩大训练集。

    返回所有切片的回测结果。
    """
    n = len(values)
    if n < min_train_size + 1:
        return ()

    slices: list[BacktestSlice] = []
    forecast_fn = {
        "seasonal_naive": _seasonal_naive_forecast,
        "ewma": _ewma_forecast,
        "regularized_trend": _regularized_trend_forecast,
    }[method]

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
        mae = round(sum(errors) / len(errors), 6)
        rmse = round(math.sqrt(sum(sq_errors) / len(sq_errors)), 6)
        # MAPE 避免除以零
        mape_vals = [
            abs(p - a) / max(abs(a), 1e-6)
            for p, a in zip(preds, test)
        ]
        mape = round(sum(mape_vals) / len(mape_vals), 4)

        slices.append(
            BacktestSlice(
                train_end_index=train_end,
                test_start_index=test_start,
                horizon=horizon,
                mae=mae,
                rmse=rmse,
                mape=mape,
                forecasts=tuple(preds),
                actuals=tuple(test),
            )
        )

    return tuple(slices)


def generate_forecast(
    subject_id: str,
    values: Sequence[float],
    windows: Sequence[str],
    *,
    method: MethodName = "regularized_trend",
    forecast_steps: int = 4,
    min_train_size: int = 3,
    **params,
) -> ForecastResult:
    """生成完整预测结果（含回测和未来预测）。"""
    n = len(values)
    if n < 2:
        return ForecastResult(
            subject_id=subject_id, method=method,
            observed_windows=tuple(windows),
            observed_values=tuple(values),
            points=tuple(
                ForecastPoint(
                    window=windows[i], observed=values[i],
                    forecast=values[i], is_observed=True,
                )
                for i in range(n)
            ),
            backtest=(),
        )

    backtest_slices = rolling_backtest(
        values, method=method, min_train_size=min_train_size, **params,
    )

    # 生成未来预测
    forecast_fn = {
        "seasonal_naive": _seasonal_naive_forecast,
        "ewma": _ewma_forecast,
        "regularized_trend": _regularized_trend_forecast,
    }[method]
    future_preds = forecast_fn(values, forecast_steps, **params)

    points: list[ForecastPoint] = []
    for i, (w, v) in enumerate(zip(windows, values)):
        points.append(
            ForecastPoint(window=w, observed=v, forecast=v, is_observed=True)
        )
    # 未来纯预测点
    last_window = windows[-1] if windows else "w0"
    for step, pred in enumerate(future_preds, start=1):
        points.append(
            ForecastPoint(
                window=f"{last_window}+{step}",
                observed=None,
                forecast=pred,
                is_observed=False,
            )
        )

    # 聚合回测指标
    if backtest_slices:
        avg_mae = round(
            sum(s.mae for s in backtest_slices) / len(backtest_slices), 6
        )
        avg_rmse = round(
            math.sqrt(
                sum(s.rmse ** 2 for s in backtest_slices) / len(backtest_slices)
            ), 6
        )
    else:
        avg_mae = 0.0
        avg_rmse = 0.0

    return ForecastResult(
        subject_id=subject_id,
        method=method,
        observed_windows=tuple(windows),
        observed_values=tuple(values),
        points=tuple(points),
        backtest=backtest_slices,
        rolling_mae=avg_mae,
        rolling_rmse=avg_rmse,
        best_params=params,
    )


# ═══════════════════════════════════════════════════════════════════
# A23: Conformal Interval（保形区间）
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ConformalInterval:
    """保形预测区间。"""
    subject_id: str
    method: str
    level: float  # 覆盖率 0~1
    lower: float
    upper: float
    interval_width: float
    is_reliable: bool  # False → insufficient_history
    reason: str = ""


def compute_conformal_interval(
    values: Sequence[float],
    *,
    subject_id: str = "",
    method: str = "regularized_trend",
    level: float = 0.80,
    min_history: int = 5,
    window_size: int = 4,
    **forecast_params,
) -> ConformalInterval:
    """基于滚动残差经验分布计算保形区间。

    在滚动回测的残差上做加性保形预测：
    - 取最近 window_size 个残差为非一致性分数
    - 排序后取 (level * window_size) 分位数作为区间半径
    - 历史不足 min_history 时输出 insufficient_history
    """
    n = len(values)
    if n < min_history:
        return ConformalInterval(
            subject_id=subject_id, method=method,
            level=level, lower=0.0, upper=0.0,
            interval_width=0.0, is_reliable=False,
            reason=f"insufficient_history: {n} < {min_history}",
        )

    forecast_fn = {
        "seasonal_naive": _seasonal_naive_forecast,
        "ewma": _ewma_forecast,
        "regularized_trend": _regularized_trend_forecast,
    }[method]

    # 滚动回测产生残差序列
    residuals: list[float] = []
    for train_end in range(2, n):
        train = values[:train_end]
        if train_end < n:
            actual = values[train_end]
            pred = forecast_fn(train, 1, **forecast_params)[0]
            residuals.append(abs(actual - pred))

    if len(residuals) < 3:
        return ConformalInterval(
            subject_id=subject_id, method=method,
            level=level, lower=0.0, upper=0.0,
            interval_width=0.0, is_reliable=False,
            reason=f"insufficient_history: only {len(residuals)} residuals",
        )

    # 最近 window_size 个残差排序取分位数
    recent_residuals = sorted(residuals[-window_size:])
    quantile_index = min(
        int(math.ceil(level * len(recent_residuals))) - 1,
        len(recent_residuals) - 1,
    )
    quantile_index = max(0, quantile_index)
    radius = recent_residuals[quantile_index]

    # 最终预测值
    final_forecast = forecast_fn(values, 1, **forecast_params)[0]
    lower = round(max(0.0, final_forecast - radius), 6)
    upper = round(min(1.0, final_forecast + radius), 6)

    return ConformalInterval(
        subject_id=subject_id,
        method=method,
        level=level,
        lower=lower,
        upper=upper,
        interval_width=round(upper - lower, 6),
        is_reliable=True,
        reason=f"based on {len(residuals)} residuals, recent_window={len(recent_residuals)}",
    )


# ═══════════════════════════════════════════════════════════════════
# A24: 层级预测一致性校正
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class HierarchyLevel:
    """层级结构中的一层。"""
    name: str
    members: tuple[str, ...]
    parent: str | None = None


@dataclass(frozen=True)
class ReconciliationResult:
    """层级一致性校正结果。"""
    method: str  # "bottom_up" | "top_down" | "mint_ols"
    level: str
    node_id: str
    original_forecast: float
    reconciled_forecast: float
    adjustment: float
    adjustment_pct: float


@dataclass(frozen=True)
class HierarchyForecastReconciliation:
    """完整层级预测 reconciliation 报告。"""
    hierarchy_name: str
    levels: tuple[HierarchyLevel, ...]
    results: tuple[ReconciliationResult, ...]
    pre_reconciliation_mae: float
    post_reconciliation_mae: float
    improvement_pct: float


def _build_hierarchy_tree(
    levels: Sequence[HierarchyLevel],
) -> dict[str, list[str]]:
    """构建父子关系树。"""
    tree: dict[str, list[str]] = defaultdict(list)
    for level in levels:
        parent = level.parent or "__root__"
        for member in level.members:
            tree[parent].append(member)
    return dict(tree)


def bottom_up_reconcile(
    forecasts: Mapping[str, float],
    levels: Sequence[HierarchyLevel],
) -> dict[str, float]:
    """自底向上一致性校正：子节点聚合到父节点。"""
    reconciled = dict(forecasts)
    # 找到最底层的节点
    leaf_members: set[str] = set()
    non_leaf: set[str] = set()
    for level in levels:
        if level.parent:
            non_leaf.add(level.parent)
        for m in level.members:
            leaf_members.add(m)
    # 真正叶子 = 不在任何 parent 中
    leaves = leaf_members - non_leaf

    # 从叶子开始向上聚合
    tree = _build_hierarchy_tree(levels)

    def _aggregate(node: str) -> float:
        children = tree.get(node, [])
        if not children or node in leaves:
            return forecasts.get(node, 0.0)
        child_sum = sum(_aggregate(c) for c in children)
        reconciled[node] = child_sum
        return child_sum

    for root_child in tree.get("__root__", []):
        _aggregate(root_child)

    return reconciled


def top_down_reconcile(
    forecasts: Mapping[str, float],
    levels: Sequence[HierarchyLevel],
    actuals: Mapping[str, float] | None = None,
) -> dict[str, float]:
    """自顶向下一致性校正：按历史比例向下分解。"""
    reconciled = dict(forecasts)
    tree = _build_hierarchy_tree(levels)

    def _distribute(node: str, total: float):
        children = tree.get(node, [])
        if not children:
            return
        # 按子节点原预测比例分配
        child_forecasts = {
            c: forecasts.get(c, 0.0) for c in children
        }
        child_sum = sum(child_forecasts.values())
        if child_sum > 0:
            for child in children:
                proportion = child_forecasts[child] / child_sum
                reconciled[child] = total * proportion
                _distribute(child, reconciled[child])

    for root_child in tree.get("__root__", []):
        _distribute(root_child, reconciled.get(root_child, 0.0))

    return reconciled


def reconcile_hierarchy(
    forecasts: Mapping[str, float],
    levels: Sequence[HierarchyLevel],
    *,
    actuals: Mapping[str, float] | None = None,
    method: Literal["bottom_up", "top_down"] = "bottom_up",
) -> HierarchyForecastReconciliation:
    """执行层级预测一致性校正并返回 reconciliation 报告。"""
    if method == "bottom_up":
        reconciled = bottom_up_reconcile(forecasts, levels)
    else:
        reconciled = top_down_reconcile(forecasts, levels, actuals)

    results: list[ReconciliationResult] = []
    for key in sorted(set(list(forecasts) + list(reconciled))):
        orig = forecasts.get(key, 0.0)
        rec = reconciled.get(key, orig)
        adj = rec - orig
        adj_pct = round(adj / max(abs(orig), 1e-6), 4) if orig != 0 else 0.0
        level_name = "unknown"
        for lv in levels:
            if key in lv.members or key == lv.parent:
                level_name = lv.name
                break

        results.append(
            ReconciliationResult(
                method=method, level=level_name, node_id=key,
                original_forecast=round(orig, 6),
                reconciled_forecast=round(rec, 6),
                adjustment=round(adj, 6),
                adjustment_pct=adj_pct,
            )
        )

    # 如果有实际值，计算 reconciliation 前后的 MAE
    pre_mae = 0.0
    post_mae = 0.0
    if actuals:
        act_entries = [(k, v) for k, v in actuals.items() if k in forecasts]
        if act_entries:
            pre_mae = round(
                sum(abs(forecasts.get(k, 0.0) - v) for k, v in act_entries)
                / len(act_entries), 6
            )
            post_mae = round(
                sum(abs(reconciled.get(k, forecasts.get(k, 0.0)) - v)
                    for k, v in act_entries) / len(act_entries), 6
            )

    improvement = (
        round((pre_mae - post_mae) / max(pre_mae, 1e-6) * 100, 2)
        if pre_mae > 0 else 0.0
    )

    return HierarchyForecastReconciliation(
        hierarchy_name=levels[0].name.split("_")[0] if levels else "unnamed",
        levels=tuple(levels),
        results=tuple(results),
        pre_reconciliation_mae=pre_mae,
        post_reconciliation_mae=post_mae,
        improvement_pct=improvement,
    )


# ═══════════════════════════════════════════════════════════════════
# A25: 领先滞后信号与伪相关检查
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class CrossCorrelationResult:
    """两个序列的互相关分析。"""
    subject_a: str
    subject_b: str
    max_correlation: float
    lag_at_max: int  # 正=A领先B，负=B领先A
    correlations: tuple[float, ...]
    lags: tuple[int, ...]
    is_significant: bool  # |max_corr| >= 0.5
    association_only: bool = True  # 始终为 True


@dataclass(frozen=True)
class StabilityCheck:
    """滚动窗口稳定性检查。"""
    subject_id: str
    window_size: int
    rolling_means: tuple[float, ...]
    rolling_stds: tuple[float, ...]
    is_stationary_heuristic: bool  # rolling mean 变化 < 30% 全局 std


@dataclass(frozen=True)
class SpuriousCorrelationWarning:
    """伪相关警告。"""
    subject_a: str
    subject_b: str
    correlation: float
    warning_type: Literal[
        "small_sample", "cherry_picked_window",
        "no_lag_consistency", "non_stationary",
    ]
    reason: str


def compute_cross_correlation(
    values_a: Sequence[float],
    values_b: Sequence[float],
    *,
    subject_a: str = "A",
    subject_b: str = "B",
    max_lag: int = 4,
    significance_threshold: float = 0.5,
) -> CrossCorrelationResult:
    """计算两个序列的互相关（cross-correlation）。

    在不同滞后期下计算 Pearson 相关系数。
    lag > 0 表示 A 领先 B。
    """
    n = len(values_a)
    if n != len(values_b) or n < 3:
        return CrossCorrelationResult(
            subject_a=subject_a, subject_b=subject_b,
            max_correlation=0.0, lag_at_max=0,
            correlations=(), lags=(),
            is_significant=False,
        )

    mean_a = sum(values_a) / n
    mean_b = sum(values_b) / n
    std_a = math.sqrt(sum((v - mean_a) ** 2 for v in values_a) / n) or 1e-9
    std_b = math.sqrt(sum((v - mean_b) ** 2 for v in values_b) / n) or 1e-9

    max_corr = -1.0
    best_lag = 0
    correlations: list[float] = []
    lags_list: list[int] = []

    for lag in range(-max_lag, max_lag + 1):
        if lag > 0:
            # A 领先 B：比较 A[0:n-lag] 与 B[lag:n]
            a_slice = values_a[:n - lag]
            b_slice = values_b[lag:]
        elif lag < 0:
            # B 领先 A：比较 A[-lag:n] 与 B[0:n+lag]
            shift = -lag
            a_slice = values_a[shift:]
            b_slice = values_b[:n - shift]
        else:
            a_slice = values_a
            b_slice = values_b

        m = len(a_slice)
        if m < 2:
            continue

        ma = sum(a_slice) / m
        mb = sum(b_slice) / m
        sa = math.sqrt(sum((v - ma) ** 2 for v in a_slice) / m) or 1e-9
        sb = math.sqrt(sum((v - mb) ** 2 for v in b_slice) / m) or 1e-9

        corr = sum(
            (a_slice[i] - ma) * (b_slice[i] - mb) for i in range(m)
        ) / (m * sa * sb)

        correlations.append(round(corr, 4))
        lags_list.append(lag)
        if abs(corr) > abs(max_corr):
            max_corr = corr
            best_lag = lag

    return CrossCorrelationResult(
        subject_a=subject_a, subject_b=subject_b,
        max_correlation=round(max_corr, 4),
        lag_at_max=best_lag,
        correlations=tuple(correlations),
        lags=tuple(lags_list),
        is_significant=abs(max_corr) >= significance_threshold,
    )


def check_stability(
    values: Sequence[float],
    *,
    subject_id: str = "",
    window_size: int = 4,
) -> StabilityCheck:
    """滚动窗口稳定性检查。"""
    n = len(values)
    if n < window_size:
        return StabilityCheck(
            subject_id=subject_id, window_size=window_size,
            rolling_means=(), rolling_stds=(),
            is_stationary_heuristic=True,
        )

    global_range = float(max(values) - min(values))

    rolling_means: list[float] = []
    rolling_stds: list[float] = []
    for i in range(n - window_size + 1):
        win = values[i:i + window_size]
        wm = sum(win) / window_size
        rolling_means.append(round(wm, 4))
        wstd = math.sqrt(
            sum((v - wm) ** 2 for v in win) / window_size
        )
        rolling_stds.append(round(wstd, 4))

    # 滚动均值的变化范围是否显著：用相对全局 range 的比例而非全局 std。
    # 稳定序列的全局 std 本身极小（如 [0.5,0.51,0.49,...] 的 std≈0.007），
    # 用「30% × std」做阈值会把正常漂移误判成非平稳；改用全局 range 度量
    # 波动幅度，滚动均值漂移超过其 50% 才视为非平稳。
    mean_range = max(rolling_means) - min(rolling_means)
    is_stationary = mean_range < 0.50 * global_range

    return StabilityCheck(
        subject_id=subject_id,
        window_size=window_size,
        rolling_means=tuple(rolling_means),
        rolling_stds=tuple(rolling_stds),
        is_stationary_heuristic=is_stationary,
    )


def detect_spurious_correlation(
    values_a: Sequence[float],
    values_b: Sequence[float],
    *,
    subject_a: str = "A",
    subject_b: str = "B",
    min_sample_size: int = 8,
) -> tuple[SpuriousCorrelationWarning, ...]:
    """检测伪相关风险。"""
    warnings: list[SpuriousCorrelationWarning] = []
    n = min(len(values_a), len(values_b))

    if n < min_sample_size:
        warnings.append(
            SpuriousCorrelationWarning(
                subject_a=subject_a, subject_b=subject_b,
                correlation=0.0,
                warning_type="small_sample",
                reason=f"仅 {n} 个样本（需要至少 {min_sample_size}）",
            )
        )

    # 检查非平稳性
    stability_a = check_stability(values_a, subject_id=subject_a)
    stability_b = check_stability(values_b, subject_id=subject_b)
    if not stability_a.is_stationary_heuristic:
        warnings.append(
            SpuriousCorrelationWarning(
                subject_a=subject_a, subject_b=subject_b,
                correlation=0.0,
                warning_type="non_stationary",
                reason=f"{subject_a} 序列非平稳（滚动均值变化过大）",
            )
        )
    if not stability_b.is_stationary_heuristic:
        warnings.append(
            SpuriousCorrelationWarning(
                subject_a=subject_a, subject_b=subject_b,
                correlation=0.0,
                warning_type="non_stationary",
                reason=f"{subject_b} 序列非平稳（滚动均值变化过大）",
            )
        )

    # 检查滞后一致性
    if n >= 5:
        xcorr = compute_cross_correlation(
            values_a, values_b, subject_a=subject_a, subject_b=subject_b,
        )
        if xcorr.is_significant:
            # 检查滞后是否一致：前后半段的最大滞后是否矛盾
            mid = n // 2
            first_half = compute_cross_correlation(
                values_a[:mid], values_b[:mid], max_lag=2,
            )
            second_half = compute_cross_correlation(
                values_a[mid:], values_b[mid:], max_lag=2,
            )
            if (
                first_half.lag_at_max != 0
                and second_half.lag_at_max != 0
                and first_half.lag_at_max * second_half.lag_at_max < 0
            ):
                warnings.append(
                    SpuriousCorrelationWarning(
                        subject_a=subject_a, subject_b=subject_b,
                        correlation=xcorr.max_correlation,
                        warning_type="no_lag_consistency",
                        reason=(
                            f"滞后方向不一致：前半段 lag={first_half.lag_at_max}，"
                            f"后半段 lag={second_half.lag_at_max}"
                        ),
                    )
                )

    return tuple(warnings)
