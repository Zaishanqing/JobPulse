"""A27: 2^4 Replay Lab — 因素交互分析与 Shapley 分解框架。

四类因素：
  D = source facts / Release（数据快照）
  C = catalog snapshot / skill mappings（目录映射）
  P = algorithm / policy / config（算法策略）
  H = human review decisions（人工审核）

本模块提供：
- 2^4 = 16 种因子组合的生成与管理
- 基于 Shapley 值的因素贡献分解
- 两因素交互效应分析
- Replay 配置的 provenance 追踪

依赖 TEMP-05 的 controlled_replay 基础设施和 A-DATA-01 的冻结版本体系。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import Literal


# ═══════════════════════════════════════════════════════════════════
# 因素定义
# ═══════════════════════════════════════════════════════════════════

FactorName = Literal["D", "C", "P", "H"]
ALL_FACTORS: tuple[FactorName, ...] = ("D", "C", "P", "H")

FACTOR_LABELS: dict[FactorName, str] = {
    "D": "Data / Source Facts",
    "C": "Catalog Snapshot / Mappings",
    "P": "Algorithm / Policy / Config",
    "H": "Human Review Decisions",
}


@dataclass(frozen=True)
class FactorState:
    """单个因素的冻结/活跃状态。"""
    factor: FactorName
    is_frozen: bool  # True = 冻结（使用基线版本），False = 活跃（使用目标版本）
    frozen_version: str = ""  # 冻结时使用的版本 ID
    live_version: str = ""    # 活跃时使用的版本 ID


# ═══════════════════════════════════════════════════════════════════
# Replay 配置
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ReplayConfig:
    """单个 replay 实验配置（2^4 中的一种组合）。"""
    config_id: str  # e.g. "R_D_C_P_H" (全活跃) or "R_d_C_P_H" (D冻结)
    label: str       # 人类可读标签
    factors: tuple[FactorState, ...]
    frozen_count: int
    active_count: int

    def is_factor_frozen(self, factor: FactorName) -> bool:
        for f in self.factors:
            if f.factor == factor:
                return f.is_frozen
        return False

    @property
    def frozen_factors(self) -> tuple[FactorName, ...]:
        return tuple(f.factor for f in self.factors if f.is_frozen)

    @property
    def active_factors(self) -> tuple[FactorName, ...]:
        return tuple(f.factor for f in self.factors if not f.is_frozen)


@dataclass(frozen=True)
class ReplayOutcome:
    """单个 replay 配置的运行结果（需实际执行后填入）。"""
    config_id: str
    metric_value: float  # 关注的结果指标（如 MAE、Jaccard 变化等）
    metric_name: str = "unknown"
    is_executed: bool = False
    error: str = ""


# ═══════════════════════════════════════════════════════════════════
# Shapley 分解
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ShapleyContribution:
    """单个因素的 Shapley 贡献。"""
    factor: FactorName
    shapley_value: float
    contribution_pct: float  # 占总解释方差的比例
    rank: int
    sign: Literal["positive", "negative", "neutral"]


@dataclass(frozen=True)
class InteractionEffect:
    """两因素交互效应。"""
    factor_a: FactorName
    factor_b: FactorName
    interaction_value: float
    is_synergistic: bool  # True = 正交互（1+1>2），False = 负交互
    magnitude: float  # 相对主效应的大小


@dataclass(frozen=True)
class ShapleyDecomposition:
    """完整的 Shapley 分解报告。"""
    baseline_value: float  # 全冻结（d_c_p_h）的基线值
    full_active_value: float  # 全活跃（D_C_P_H）的值
    total_effect: float  # full_active - baseline
    contributions: tuple[ShapleyContribution, ...]
    interactions: tuple[InteractionEffect, ...]
    unexplained: float  # 未被主效应+交互解释的残差
    r_squared: float  # 主效应+交互的解释度


@dataclass(frozen=True)
class ReplayLabReport:
    """完整 Replay Lab 分析报告。"""
    title: str
    factors: tuple[FactorState, ...]
    all_configs: tuple[ReplayConfig, ...]
    outcomes: tuple[ReplayOutcome, ...]
    decomposition: ShapleyDecomposition
    limitations: tuple[str, ...]
    is_complete: bool  # 是否所有 16 种配置都已执行


# ═══════════════════════════════════════════════════════════════════
# 配置生成
# ═══════════════════════════════════════════════════════════════════

def generate_replay_configs(
    *,
    frozen_versions: Mapping[FactorName, str] | None = None,
    live_versions: Mapping[FactorName, str] | None = None,
) -> tuple[ReplayConfig, ...]:
    """生成全部 2^4 = 16 种因子组合的 Replay 配置。

    config_id 命名规则：
    - 大写字母 = 活跃（live），如 D
    - 小写字母 = 冻结（frozen），如 d
    - 全冻结：R_d_c_p_h，全活跃：R_D_C_P_H
    """
    if frozen_versions is None:
        frozen_versions = {}
    if live_versions is None:
        live_versions = {}

    configs: list[ReplayConfig] = []

    # 遍历所有子集作为"冻结"的因子（共16种组合）
    for r in range(5):  # 0..4 个冻结因子
        for frozen_set in combinations(ALL_FACTORS, r):
            frozen_set_set = set(frozen_set)
            factors: list[FactorState] = []
            config_parts: list[str] = ["R"]

            for factor in ALL_FACTORS:
                is_frozen = factor in frozen_set_set
                fv = frozen_versions.get(factor, f"baseline_{factor}")
                lv = live_versions.get(factor, f"target_{factor}")
                factors.append(
                    FactorState(
                        factor=factor,
                        is_frozen=is_frozen,
                        frozen_version=fv,
                        live_version=lv,
                    )
                )
                config_parts.append(factor.lower() if is_frozen else factor.upper())

            config_id = "_".join(config_parts)
            frozen_desc = ", ".join(
                FACTOR_LABELS[f.factor] for f in factors if f.is_frozen
            ) or "无"
            label = f"[冻结: {frozen_desc}]"

            configs.append(
                ReplayConfig(
                    config_id=config_id,
                    label=label,
                    factors=tuple(factors),
                    frozen_count=len(frozen_set),
                    active_count=4 - len(frozen_set),
                )
            )

    return tuple(configs)


# ═══════════════════════════════════════════════════════════════════
# Shapley 值计算
# ═══════════════════════════════════════════════════════════════════

def _coalition_value(
    active_factors: set[FactorName],
    outcomes_by_frozen: dict[frozenset[FactorName], float],
) -> float:
    """返回「恰好 active_factors 活跃、其余冻结」时的结果指标值。

    ``outcomes_by_frozen`` 以冻结因子集合为 key；这里把活跃集合转成冻结集合
    （全集 - 活跃）再查。缺失的 coalition 用已知冻结集合中交集最近者估计。
    """
    frozen = frozenset(ALL_FACTORS) - frozenset(active_factors)
    if frozen in outcomes_by_frozen:
        return outcomes_by_frozen[frozen]
    if not outcomes_by_frozen:
        return 0.0
    best_key = max(
        outcomes_by_frozen,
        key=lambda k: len(k & frozen),
        default=frozenset(),
    )
    return outcomes_by_frozen.get(best_key, 0.0)


def compute_shapley_decomposition(
    outcomes: Mapping[str, float],
    *,
    metric_name: str = "unknown",
    baseline_config_id: str = "R_d_c_p_h",
    full_active_config_id: str = "R_D_C_P_H",
) -> ShapleyDecomposition:
    """从实验结果的指标值计算 Shapley 分解。

    outcomes: {config_id: metric_value, ...} 至少需要包含全冻结和全活跃的基线。
    不必所有 16 种配置都执行；缺失的 coalition 值用已知最近值估计。

    Shapley value formula:
      phi_i = sum_{S subset N\\{i}}  |S|!(|N|-|S|-1)! / |N|!  *  [v(S union {i}) - v(S)]
    """
    # 解析 config_id → frozen_factors 映射
    def _parse_frozen(config_id: str) -> frozenset[FactorName]:
        if config_id.startswith("R_"):
            parts = config_id.split("_")[1:]
        else:
            parts = config_id.split("_")
        frozen = set()
        for part in parts:
            if part and part[0].islower():
                upper = part[0].upper()
                if upper in ALL_FACTORS:
                    frozen.add(upper)
        return frozenset(frozen)

    outcomes_by_frozen: dict[frozenset[FactorName], float] = {}
    for cid, val in outcomes.items():
        outcomes_by_frozen[_parse_frozen(cid)] = val

    baseline = outcomes_by_frozen.get(
        frozenset(ALL_FACTORS),
        outcomes.get(baseline_config_id, 0.0),
    )
    full_active = outcomes_by_frozen.get(
        frozenset(),
        outcomes.get(full_active_config_id, 0.0),
    )
    total_effect = full_active - baseline

    n = len(ALL_FACTORS)
    shapley_values: dict[FactorName, float] = {}

    for factor in ALL_FACTORS:
        phi = 0.0
        other_factors = set(ALL_FACTORS) - {factor}

        for r in range(n):  # |S| = r
            weight = (
                math.factorial(r) * math.factorial(n - r - 1)
                / math.factorial(n)
            )
            # 遍历所有不包含 factor 的大小为 r 的子集
            for S in combinations(other_factors, r):
                S_set = set(S)
                v_without = _coalition_value(S_set, outcomes_by_frozen)
                v_with = _coalition_value(S_set | {factor}, outcomes_by_frozen)
                phi += weight * (v_with - v_without)

        shapley_values[factor] = round(phi, 6)

    # 排序并计算贡献百分比
    total_shapley = sum(abs(v) for v in shapley_values.values()) or 1e-9
    sorted_factors = sorted(
        shapley_values.items(), key=lambda x: abs(x[1]), reverse=True,
    )

    contributions: list[ShapleyContribution] = []
    for rank, (factor, value) in enumerate(sorted_factors, start=1):
        pct = round(value / total_shapley * 100, 2) if total_shapley > 0 else 0.0
        if abs(value) < 1e-9:
            sign_val: Literal["positive", "negative", "neutral"] = "neutral"
        elif value > 0:
            sign_val = "positive"
        else:
            sign_val = "negative"
        contributions.append(
            ShapleyContribution(
                factor=factor,
                shapley_value=value,
                contribution_pct=pct,
                rank=rank,
                sign=sign_val,
            )
        )

    # 两因素交互效应
    interactions = _compute_interactions(outcomes_by_frozen, shapley_values)

    # 主效应（Shapley 效率性）应完整复现总效应；交互是额外的两两分解项。
    # unexplained 定义为使「主效应 + 交互 + 未解释 = 总效应」成立的残差。
    main_effect = sum(c.shapley_value for c in contributions)
    interaction_total = sum(i.interaction_value for i in interactions)
    unexplained = round(total_effect - main_effect - interaction_total, 6)
    # r_squared 衡量主效应的解释度（Shapley 效率性残差比例），交互不参与。
    r_squared = round(
        1.0 - abs(total_effect - main_effect) / max(abs(total_effect), 1e-9), 4
    )

    return ShapleyDecomposition(
        baseline_value=round(baseline, 6),
        full_active_value=round(full_active, 6),
        total_effect=round(total_effect, 6),
        contributions=tuple(contributions),
        interactions=tuple(interactions),
        unexplained=unexplained,
        r_squared=r_squared,
    )


def _compute_interactions(
    outcomes_by_frozen: dict[frozenset[FactorName], float],
    shapley_values: dict[FactorName, float],
) -> list[InteractionEffect]:
    """计算所有两因素交互效应。"""
    interactions: list[InteractionEffect] = []

    for factor_a, factor_b in combinations(ALL_FACTORS, 2):
        # 交互效应估计：
        # 冻结 A+B 的效果 vs 分别冻结 A 和 B 的效果之和
        v_ab = _coalition_value({factor_a, factor_b}, outcomes_by_frozen)
        v_a = _coalition_value({factor_a}, outcomes_by_frozen)
        v_b = _coalition_value({factor_b}, outcomes_by_frozen)
        v_0 = _coalition_value(set(), outcomes_by_frozen)

        individual = (v_a - v_0) + (v_b - v_0)
        interaction = v_ab - v_0 - individual
        interaction = round(interaction, 6)

        total_main = abs(shapley_values.get(factor_a, 0)) + abs(
            shapley_values.get(factor_b, 0)
        )
        magnitude = round(
            abs(interaction) / max(total_main, 1e-9), 4
        )

        interactions.append(
            InteractionEffect(
                factor_a=factor_a,
                factor_b=factor_b,
                interaction_value=interaction,
                is_synergistic=interaction > 0,
                magnitude=magnitude,
            )
        )

    return interactions


# ═══════════════════════════════════════════════════════════════════
# 因素重要性排名（无需全部 16 种配置）
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class FactorImportance:
    """单因素重要性估计（基于有限实验）。"""
    factor: FactorName
    label: str
    main_effect: float  # 冻结此因素造成的指标变化
    effect_pct: float
    rank: int


def estimate_factor_importance(
    outcomes: Mapping[str, float],
    *,
    baseline_config_id: str = "R_d_c_p_h",
) -> tuple[FactorImportance, ...]:
    """从最少 5 个配置（baseline + 4个单因素活跃）估计因素重要性。

    不需要全部 16 种配置，只需要：
    - 全冻结 baseline（R_d_c_p_h）
    - 单独激活每个因素的配置（R_D_c_p_h, R_d_C_p_h, R_d_c_P_h, R_d_c_p_H）
    """
    baseline = outcomes.get(baseline_config_id, 0.0)

    single_active_configs: dict[FactorName, str] = {
        "D": "R_D_c_p_h",
        "C": "R_d_C_p_h",
        "P": "R_d_c_P_h",
        "H": "R_d_c_p_H",
    }

    effects: list[tuple[FactorName, float]] = []
    for factor in ALL_FACTORS:
        config_id = single_active_configs[factor]
        val = outcomes.get(config_id)
        if val is not None:
            effect = val - baseline
        else:
            effect = 0.0
        effects.append((factor, effect))

    total_effect = sum(abs(e) for _, e in effects) or 1e-9
    sorted_effects = sorted(effects, key=lambda x: abs(x[1]), reverse=True)

    results: list[FactorImportance] = []
    for rank, (factor, effect) in enumerate(sorted_effects, start=1):
        results.append(
            FactorImportance(
                factor=factor,
                label=FACTOR_LABELS[factor],
                main_effect=round(effect, 6),
                effect_pct=round(abs(effect) / total_effect * 100, 2),
                rank=rank,
            )
        )

    return tuple(results)


# ═══════════════════════════════════════════════════════════════════
# Replay Lab 综合分析
# ═══════════════════════════════════════════════════════════════════

def create_replay_lab_report(
    outcomes: Mapping[str, float],
    *,
    title: str = "Replay Lab Analysis",
    metric_name: str = "unknown",
    frozen_versions: Mapping[FactorName, str] | None = None,
    live_versions: Mapping[FactorName, str] | None = None,
    limitations: Sequence[str] = (),
) -> ReplayLabReport:
    """从实验结果创建完整的 Replay Lab 分析报告。"""
    configs = generate_replay_configs(
        frozen_versions=frozen_versions,
        live_versions=live_versions,
    )

    outcome_objects: list[ReplayOutcome] = []
    for config in configs:
        val = outcomes.get(config.config_id)
        outcome_objects.append(
            ReplayOutcome(
                config_id=config.config_id,
                metric_value=round(val, 6) if val is not None else 0.0,
                metric_name=metric_name,
                is_executed=val is not None,
                error="" if val is not None else "未执行",
            )
        )

    executed_outcomes = {
        cid: val for cid, val in outcomes.items()
    }

    decomposition = compute_shapley_decomposition(
        executed_outcomes,
        metric_name=metric_name,
    )

    all_limitations = list(limitations)
    total_executed = sum(1 for o in outcome_objects if o.is_executed)
    if total_executed < 16:
        all_limitations.append(
            f"仅 {total_executed}/16 配置已执行，Shapley 分解可能不精确"
        )

    return ReplayLabReport(
        title=title,
        factors=tuple(
            FactorState(
                factor=f,
                is_frozen=False,
                frozen_version=(frozen_versions or {}).get(f, ""),
                live_version=(live_versions or {}).get(f, ""),
            )
            for f in ALL_FACTORS
        ),
        all_configs=configs,
        outcomes=tuple(outcome_objects),
        decomposition=decomposition,
        limitations=tuple(all_limitations),
        is_complete=total_executed == 16,
    )


# ═══════════════════════════════════════════════════════════════════
# 实用工具
# ═══════════════════════════════════════════════════════════════════

def config_id_to_human(config_id: str) -> str:
    """将 config_id 转换为人类可读描述。"""
    if config_id.startswith("R_"):
        parts = config_id.split("_")[1:]
    else:
        parts = config_id.split("_")

    descriptions: list[str] = []
    for part in parts:
        if not part:
            continue
        factor = part[0].upper()
        if factor in FACTOR_LABELS:
            label = FACTOR_LABELS[factor]
            if part[0].islower():
                descriptions.append(f"{label}（冻结）")
            else:
                descriptions.append(f"{label}（活跃）")

    return " | ".join(descriptions) if descriptions else config_id


def get_minimal_config_ids() -> dict[str, str]:
    """获取最小实验集合（5 个配置）的 config_id。

    用于在仅执行 D-only replay 时提供因素重要性估计。
    """
    return {
        "baseline_all_frozen": "R_d_c_p_h",
        "only_D_active": "R_D_c_p_h",
        "only_C_active": "R_d_C_p_h",
        "only_P_active": "R_d_c_P_h",
        "only_H_active": "R_d_c_p_H",
        "all_active": "R_D_C_P_H",
    }
