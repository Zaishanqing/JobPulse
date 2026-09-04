from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from math import sqrt


TREND_CHANGE_METHOD = "rolling_baseline_zscore"
TURNING_POINT_METHOD = "trend_direction_reversal"
DEFAULT_ALGORITHM_VERSION = "trend-change.v1"
DEFAULT_CONFIG_VERSION = "trend-change.v1"

CONFIDENCE_WEIGHTS = {
    "change_magnitude": 0.35,
    "persistence": 0.25,
    "source_diversity": 0.20,
    "window_stability": 0.20,
}

DEFAULT_TREND_CHANGE_CONFIG = {
    "positive_threshold": 0.04,
    "negative_threshold": -0.04,
    "acceleration_threshold": 0.02,
    "stable_threshold": 0.03,
    "volatility_threshold": 0.40,
    "baseline_windows": 3,
    "change_z_threshold": 2.0,
    "min_abs_change": 0.15,
    "min_persistence_windows": 2,
    "turning_directional_consistency": 0.75,
    "turning_max_reverse_ratio": 0.25,
    "stability_window_size": 4,
    "volatility_window_size": 4,
    "max_source_diversity": 6,
    "epsilon": 1e-9,
    # absolute support 门禁：序列的绝对证据强度（各窗口 absolute_support 的峰值）
    # 低于该阈值时，不产出 declining/rising/volatile 等强趋势结论，返回
    # insufficient_evidence（低量噪声主题，如 A9 的 robot/semiconductor）。
    # 默认 0.6：低于 n_expected 的 60% 证据强度视为低量。
    "min_absolute_support": 0.6,
}


@dataclass(frozen=True)
class TrendWindowScore:
    subject_id: str
    subject_type: str
    window: str
    score: float
    duration_days: float = 1.0
    source_diversity: int = 0
    source_scores: dict[str, float] = field(default_factory=dict)
    source_records: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    trend_report_id: str | None = None
    analysis_run_id: str | None = None
    source_count: int = 0
    algorithm_version: str | None = None
    config_version: str | None = None
    window_start: str | None = None
    window_end: str | None = None
    # 绝对证据强度（0~1），由调用方用 absolute support（如 log 归一化）计算。
    # 用于过滤低量噪声主题：absolute_support 过低的序列不应产出 declining/rising 等
    # 强趋势结论（P1-04 / A9 的 absolute support 门禁）。
    absolute_support: float = 1.0


@dataclass(frozen=True)
class TrendWindowPoint:
    window: str
    score: float
    absolute_growth: float | None = None
    relative_growth: float | None = None
    growth_rate: float | None = None
    acceleration: float | None = None
    window_stability: float = 1.0
    volatility: float = 0.0
    is_change_point: bool = False
    # 相对增长的可比基线状态：
    # - "comparable": 前序分数 > 0，relative_growth 为正常同比/环比
    # - "newly_observed": 前序分数 == 0 且当前分数 > 0，relative_growth 置 None，不构造伪 +100%
    # - "no_baseline": 前序分数 == 0 且当前分数 == 0，relative_growth 置 None，不显示伪 +0% 基线
    # - None: 序列首窗，无前序窗口可比
    baseline_status: str | None = None


@dataclass(frozen=True)
class TrendChangePoint:
    subject_id: str
    subject_type: str
    change_point_window: str
    direction: str
    before_mean: float
    after_mean: float
    magnitude: float
    growth_rate: float
    acceleration: float | None
    confidence: float
    method: str
    algorithm_version: str
    evidence: dict[str, object]
    lineage: dict[str, object]


@dataclass(frozen=True)
class TrendSubjectAnalysis:
    subject_id: str
    subject_type: str
    windows: tuple[TrendWindowPoint, ...]
    trend_state: str
    growth_rate: float | None
    acceleration: float | None
    absolute_growth: float | None
    relative_growth: float | None
    window_stability: float
    volatility: float
    state_confidence: float
    change_point_confidence: float | None
    confidence: float
    change_points: tuple[TrendChangePoint, ...]
    evidence: dict[str, object]
    lineage: dict[str, object]
    algorithm_version: str
    config_version: str
    config: dict[str, object] = field(default_factory=lambda: dict(DEFAULT_TREND_CHANGE_CONFIG))


def _rounded(value: float) -> float:
    return round(value, 6)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _population_std(values: Sequence[float]) -> float:
    mean = _mean(values)
    return sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def _direction(delta: float, epsilon: float) -> str:
    if delta > epsilon:
        return "up"
    if delta < -epsilon:
        return "down"
    return "flat"


def _window_stability(scores: Sequence[float], index: int, config: Mapping[str, object]) -> float:
    size = int(config.get("stability_window_size", 4))
    epsilon = float(config.get("epsilon", 1e-9))
    start = max(1, index - size + 1)
    deltas = [scores[position] - scores[position - 1] for position in range(start, index + 1)]
    if not deltas:
        return 1.0
    counts = Counter(_direction(delta, epsilon) for delta in deltas)
    return _rounded(max(counts.values()) / len(deltas))


def _volatility(scores: Sequence[float], index: int, config: Mapping[str, object]) -> float:
    size = int(config.get("volatility_window_size", 4))
    start = max(0, index - size + 1)
    recent = scores[start : index + 1]
    epsilon = float(config.get("epsilon", 1e-9))
    return _rounded(_population_std(recent) / max(abs(_mean(recent)), epsilon))


def _classify_state(
    growth_rate: float | None,
    acceleration: float | None,
    volatility: float,
    config: Mapping[str, object],
) -> str:
    if volatility > float(config.get("volatility_threshold", 0.40)):
        return "volatile"
    if growth_rate is None:
        return "stable"
    positive = float(config.get("positive_threshold", 0.04))
    negative = float(config.get("negative_threshold", -0.04))
    acceleration_threshold = float(config.get("acceleration_threshold", 0.02))
    if growth_rate > positive and acceleration is not None and acceleration > acceleration_threshold:
        return "accelerating"
    if growth_rate > positive:
        return "rising"
    if growth_rate < negative:
        return "declining"
    if abs(growth_rate) <= float(config.get("stable_threshold", 0.03)):
        return "stable"
    return "stable"


def _state_confidence(
    trend_state: str,
    growth_rate: float | None,
    acceleration: float | None,
    volatility: float,
    window_stability: float,
    config: Mapping[str, object],
) -> float:
    if trend_state == "insufficient_evidence":
        return 0.0
    epsilon = float(config.get("epsilon", 1e-9))
    if trend_state == "volatile":
        signal = min(
            volatility / max(float(config.get("volatility_threshold", 0.40)), epsilon),
            1.0,
        )
    elif trend_state == "accelerating":
        growth_signal = max(float(growth_rate or 0.0), 0.0) / max(
            float(config.get("positive_threshold", 0.04)), epsilon
        )
        acceleration_signal = max(float(acceleration or 0.0), 0.0) / max(
            float(config.get("acceleration_threshold", 0.02)), epsilon
        )
        signal = min(max(growth_signal, acceleration_signal), 1.0)
    elif trend_state == "rising":
        signal = min(
            max(float(growth_rate or 0.0), 0.0)
            / max(float(config.get("positive_threshold", 0.04)), epsilon),
            1.0,
        )
    elif trend_state == "declining":
        signal = min(
            abs(min(float(growth_rate or 0.0), 0.0))
            / max(abs(float(config.get("negative_threshold", -0.04))), epsilon),
            1.0,
        )
    else:
        signal = 1.0 - min(
            abs(float(growth_rate or 0.0))
            / max(float(config.get("stable_threshold", 0.03)), epsilon),
            1.0,
        )
    return _rounded(0.6 * signal + 0.4 * window_stability)


def _baseline_values(scores: Sequence[float], index: int, config: Mapping[str, object]) -> list[float]:
    count = int(config.get("baseline_windows", 3))
    return list(scores[max(0, index - count) : index])


def _change_candidate(
    scores: Sequence[float],
    index: int,
    config: Mapping[str, object],
) -> dict[str, object] | None:
    baseline = _baseline_values(scores, index, config)
    if len(baseline) < 2:
        return None
    mean = _mean(baseline)
    epsilon = float(config.get("epsilon", 1e-9))
    z_score = (scores[index] - mean) / max(_population_std(baseline), epsilon)
    if abs(z_score) < float(config.get("change_z_threshold", 2.0)):
        return None
    if abs(scores[index] - mean) < float(config.get("min_abs_change", 0.15)):
        return None
    return {
        "z_score": z_score,
        "mean": mean,
        "std": _population_std(baseline),
        "baseline_start": index - len(baseline),
    }


def _change_confidence(
    before_mean: float,
    after_mean: float,
    persistence_count: int,
    source_diversity: int,
    window_stability: float,
    config: Mapping[str, object],
) -> float:
    epsilon = float(config.get("epsilon", 1e-9))
    magnitude = min(abs(after_mean - before_mean) / max(abs(before_mean), epsilon), 1.0)
    persistence = min(
        persistence_count / max(int(config.get("min_persistence_windows", 2)), 1),
        1.0,
    )
    diversity = min(
        source_diversity / max(int(config.get("max_source_diversity", 6)), 1),
        1.0,
    )
    return _rounded(
        CONFIDENCE_WEIGHTS["change_magnitude"] * magnitude
        + CONFIDENCE_WEIGHTS["persistence"] * persistence
        + CONFIDENCE_WEIGHTS["source_diversity"] * diversity
        + CONFIDENCE_WEIGHTS["window_stability"] * window_stability
    )


def _turning_point_candidates(
    scores: Sequence[float],
    config: Mapping[str, object],
) -> list[tuple[int, str]]:
    """Locate trend-direction turning points the rolling z-score misses.

    The rolling-baseline z-score only sees abrupt jumps (its ``min_abs_change``
    is measured against the recent baseline mean). A gradual, sustained trend
    reversal — a monotonic rise to a peak then a monotonic decline, or the
    mirror trough — never crosses that threshold, so real change points on
    gently trending series are dropped.

    A turning point is a strict local extremum whose post-extremum sequence is
    directionally consistent in the opposite direction, covering at least
    ``min_persistence_windows``, and whose cumulative move exceeds
    ``min_abs_change``. A minority of small counter-direction steps is allowed.
    Returns ``(index, direction)`` where direction is "declining" for a peak
    followed by a decline and "rising" for a trough followed by a rise.
    """
    epsilon = float(config.get("epsilon", 1e-9))
    min_abs_change = float(config.get("min_abs_change", 0.15))
    min_persistence = max(int(config.get("min_persistence_windows", 2)), 1)
    min_consistency = float(config.get("turning_directional_consistency", 0.75))
    max_reverse_step = min_abs_change * float(
        config.get("turning_max_reverse_ratio", 0.25)
    )

    def direction_is_consistent(tail: Sequence[float], direction: str) -> bool:
        deltas = [tail[index + 1] - tail[index] for index in range(len(tail) - 1)]
        if direction == "declining":
            consistent = sum(delta <= epsilon for delta in deltas)
            reverse_steps = [delta for delta in deltas if delta > epsilon]
        else:
            consistent = sum(delta >= -epsilon for delta in deltas)
            reverse_steps = [-delta for delta in deltas if delta < -epsilon]
        return (
            consistent / len(deltas) >= min_consistency
            and max(reverse_steps, default=0.0) <= max_reverse_step
        )

    candidates: list[tuple[int, str]] = []
    n = len(scores)
    for index in range(1, n - 1):
        is_peak = (
            scores[index] > scores[index - 1] + epsilon
            and scores[index] > scores[index + 1] + epsilon
        )
        is_trough = (
            scores[index] < scores[index - 1] - epsilon
            and scores[index] < scores[index + 1] - epsilon
        )
        if not (is_peak or is_trough):
            continue
        # 拐点后的第一步必须是平缓的：若第一步即突变，说明该序列属于
        # z-score 突变型，其 persistence 语义由 z-score 负责，turning point
        # 不得重复补一个拐点。
        if abs(scores[index + 1] - scores[index]) >= min_abs_change:
            continue
        if is_peak:
            tail = scores[index:]
            if len(tail) - 1 < min_persistence:
                continue
            if not direction_is_consistent(tail, "declining"):
                continue
            if (
                scores[index] - scores[-1] >= min_abs_change
                and not any(direction == "declining" for _, direction in candidates)
            ):
                candidates.append((index, "declining"))
        elif is_trough:
            tail = scores[index:]
            if len(tail) - 1 < min_persistence:
                continue
            if not direction_is_consistent(tail, "rising"):
                continue
            if (
                scores[-1] - scores[index] >= min_abs_change
                and not any(direction == "rising" for _, direction in candidates)
            ):
                candidates.append((index, "rising"))
    return candidates


def _relative_growth_and_status(
    previous_score: float, current_score: float
) -> tuple[float | None, str | None]:
    """Return (relative_growth, baseline_status) with zero-baseline semantics.

    - previous != 0: normal comparable growth.
    - previous == 0 and current > 0: newly observed; relative growth is undefined
      (do NOT fabricate +100%).
    - previous == 0 and current <= 0: no comparable baseline; also do not fabricate
      +0% / +100%.
    """
    if previous_score != 0:
        return (current_score - previous_score) / previous_score, "comparable"
    if current_score > 0:
        return None, "newly_observed"
    return None, "no_baseline"


def analyze_trend_series(
    subject_id: str,
    subject_type: str,
    windows: Sequence[TrendWindowScore],
    *,
    algorithm_version: str = DEFAULT_ALGORITHM_VERSION,
    config_version: str = DEFAULT_CONFIG_VERSION,
    config: Mapping[str, object] | None = None,
) -> TrendSubjectAnalysis:
    if len(windows) < 2:
        raise ValueError("at least two trend windows are required")
    ordered = sorted(
        windows,
        key=lambda item: (item.window_start or item.window, item.window),
    )
    labels = [item.window for item in ordered]
    if len(set(labels)) != len(labels):
        raise ValueError("trend window labels must be unique")
    effective_config = config or DEFAULT_TREND_CHANGE_CONFIG
    scores = [item.score for item in ordered]
    epsilon = float(effective_config.get("epsilon", 1e-9))
    durations = [max(float(item.duration_days or 1.0), epsilon) for item in ordered]
    normalize_by_duration = len(set(durations)) > 1

    points: list[TrendWindowPoint] = []
    growth_rates: list[float | None] = [None]
    absolute_growths: list[float | None] = [None]
    relative_growths: list[float | None] = [None]
    accelerations: list[float | None] = [None]
    for index, item in enumerate(ordered):
        stability = _window_stability(scores, index, effective_config)
        volatility = _volatility(scores, index, effective_config)
        if index == 0:
            points.append(
                TrendWindowPoint(
                    window=item.window,
                    score=_rounded(item.score),
                    window_stability=stability,
                    volatility=volatility,
                )
            )
            continue
        absolute = item.score - ordered[index - 1].score
        growth = (
            absolute / durations[index]
            if normalize_by_duration
            else absolute
        )
        previous_score = ordered[index - 1].score
        relative, baseline_status = _relative_growth_and_status(previous_score, item.score)
        acceleration = (
            growth - growth_rates[index - 1]
            if growth_rates[index - 1] is not None
            else None
        )
        growth_rates.append(growth)
        absolute_growths.append(absolute)
        relative_growths.append(relative)
        accelerations.append(acceleration)
        points.append(
            TrendWindowPoint(
                window=item.window,
                score=_rounded(item.score),
                absolute_growth=_rounded(absolute),
                relative_growth=_rounded(relative) if relative is not None else None,
                growth_rate=_rounded(growth),
                acceleration=_rounded(acceleration) if acceleration is not None else None,
                window_stability=stability,
                volatility=volatility,
                baseline_status=baseline_status,
            )
        )

    candidates = [_change_candidate(scores, index, effective_config) for index in range(len(scores))]
    min_persistence = max(int(effective_config.get("min_persistence_windows", 2)), 1)
    runs: list[tuple[int, int, dict[str, object]]] = []
    run_start: int | None = None
    run_count = 0
    for index, candidate in enumerate(candidates):
        if candidate is None:
            if run_start is not None and run_count >= min_persistence:
                runs.append((run_start, run_count, candidates[run_start] or {}))
            run_start = None
            run_count = 0
            continue
        if run_start is None:
            run_start = index
        run_count += 1
    if run_start is not None and run_count >= min_persistence:
        runs.append((run_start, run_count, candidates[run_start] or {}))

    change_points: list[TrendChangePoint] = []
    change_window_ids: set[str] = set()
    for start_index, run_count, candidate in runs:
        trigger = ordered[start_index]
        baseline_start = int(candidate["baseline_start"])
        baseline_window_ids = labels[baseline_start:start_index]
        persistent_window_ids = labels[start_index : start_index + run_count]
        before_mean = float(candidate["mean"])
        after_mean = _mean(scores[start_index:])
        direction = "rising" if after_mean >= before_mean else "declining"
        magnitude = abs(after_mean - before_mean)
        stability = points[start_index].window_stability
        confidence = _change_confidence(
            before_mean,
            after_mean,
            run_count,
            trigger.source_diversity,
            stability,
            effective_config,
        )
        evidence = {
            "trigger_window": trigger.window,
            "direction": direction,
            "baseline_windows": baseline_window_ids,
            "persistent_windows": persistent_window_ids,
            "before_mean": _rounded(before_mean),
            "after_mean": _rounded(after_mean),
            "z_score": _rounded(float(candidate["z_score"])),
            "thresholds": {
                "change_z_threshold": float(effective_config.get("change_z_threshold", 2.0)),
                "min_abs_change": float(effective_config.get("min_abs_change", 0.15)),
                "min_persistence_windows": int(
                    effective_config.get("min_persistence_windows", 2)
                ),
            },
        }
        lineage = {
            "input_scores": [{"window": item.window, "score": item.score} for item in ordered],
            "source_records": sorted(
                {record for item in ordered for record in item.source_records}
            ),
            "evidence_ids": sorted(
                {evidence_id for item in ordered for evidence_id in item.evidence_ids}
            ),
            "trend_report_ids": sorted(
                {item.trend_report_id for item in ordered if item.trend_report_id}
            ),
            "analysis_run_ids": sorted(
                {item.analysis_run_id for item in ordered if item.analysis_run_id}
            ),
            "algorithm_version": algorithm_version,
            "config_version": config_version,
        }
        change_points.append(
            TrendChangePoint(
                subject_id=subject_id,
                subject_type=subject_type,
                change_point_window=trigger.window,
                direction=direction,
                before_mean=_rounded(before_mean),
                after_mean=_rounded(after_mean),
                magnitude=_rounded(magnitude),
                growth_rate=_rounded(float(growth_rates[start_index])),
                acceleration=(
                    _rounded(float(accelerations[start_index]))
                    if accelerations[start_index] is not None
                    else None
                ),
                confidence=confidence,
                method=TREND_CHANGE_METHOD,
                algorithm_version=algorithm_version,
                evidence=evidence,
                lineage=lineage,
            )
        )
        change_window_ids.add(trigger.window)

    # 方向反转拐点补充：z-score 只测突变，对平缓持续趋势的拐点不敏感。
    # 对 z-score 未覆盖的窗口，若存在「单调反转且持续到序列末端」的拐点，
    # 作为 change point 补充进来，避免缓降/缓升趋势完全丢失 CP。
    for turning_index, direction in _turning_point_candidates(scores, effective_config):
        window_label = labels[turning_index]
        if window_label in change_window_ids:
            continue
        # 与已有 z-score CP 相邻（±1 窗口）视为同一拐点，避免重复计数。
        if any(
            labels[neighbor] in change_window_ids
            for neighbor in (
                turning_index - 1,
                turning_index + 1,
            )
            if 0 <= neighbor < len(labels)
        ):
            continue
        trigger = ordered[turning_index]
        before_mean = _mean(scores[:turning_index])
        after_mean = _mean(scores[turning_index:])
        magnitude = abs(after_mean - before_mean)
        persistence_count = len(scores) - turning_index
        stability = points[turning_index].window_stability
        confidence = _change_confidence(
            before_mean,
            after_mean,
            persistence_count,
            trigger.source_diversity,
            stability,
            effective_config,
        )
        evidence = {
            "trigger_window": trigger.window,
            "direction": direction,
            "before_mean": _rounded(before_mean),
            "after_mean": _rounded(after_mean),
            "method": TURNING_POINT_METHOD,
            "thresholds": {
                "min_abs_change": float(effective_config.get("min_abs_change", 0.15)),
                "min_persistence_windows": int(
                    effective_config.get("min_persistence_windows", 2)
                ),
                "turning_directional_consistency": float(
                    effective_config.get("turning_directional_consistency", 0.75)
                ),
                "turning_max_reverse_ratio": float(
                    effective_config.get("turning_max_reverse_ratio", 0.25)
                ),
            },
        }
        lineage = {
            "input_scores": [{"window": item.window, "score": item.score} for item in ordered],
            "source_records": sorted(
                {record for item in ordered for record in item.source_records}
            ),
            "evidence_ids": sorted(
                {evidence_id for item in ordered for evidence_id in item.evidence_ids}
            ),
            "trend_report_ids": sorted(
                {item.trend_report_id for item in ordered if item.trend_report_id}
            ),
            "analysis_run_ids": sorted(
                {item.analysis_run_id for item in ordered if item.analysis_run_id}
            ),
            "algorithm_version": algorithm_version,
            "config_version": config_version,
        }
        change_points.append(
            TrendChangePoint(
                subject_id=subject_id,
                subject_type=subject_type,
                change_point_window=trigger.window,
                direction=direction,
                before_mean=_rounded(before_mean),
                after_mean=_rounded(after_mean),
                magnitude=_rounded(magnitude),
                growth_rate=_rounded(float(growth_rates[turning_index])),
                acceleration=(
                    _rounded(float(accelerations[turning_index]))
                    if accelerations[turning_index] is not None
                    else None
                ),
                confidence=confidence,
                method=TURNING_POINT_METHOD,
                algorithm_version=algorithm_version,
                evidence=evidence,
                lineage=lineage,
            )
        )
        change_window_ids.add(window_label)

    # absolute support 门禁：低量噪声主题不产出强趋势结论，也不保留 change point。
    max_absolute_support = max((item.absolute_support for item in ordered), default=1.0)
    insufficient = max_absolute_support < float(
        effective_config.get("min_absolute_support", 0.0)
    )
    if insufficient:
        change_points = []
        change_window_ids = set()

    points = [
        TrendWindowPoint(
            window=item.window,
            score=item.score,
            absolute_growth=(
                _rounded(absolute_growths[index])
                if absolute_growths[index] is not None
                else None
            ),
            relative_growth=(
                _rounded(relative_growths[index])
                if relative_growths[index] is not None
                else None
            ),
            growth_rate=(
                _rounded(growth_rates[index])
                if growth_rates[index] is not None
                else None
            ),
            acceleration=(
                _rounded(accelerations[index])
                if accelerations[index] is not None
                else None
            ),
            window_stability=item.window_stability,
            volatility=item.volatility,
            is_change_point=item.window in change_window_ids,
            baseline_status=item.baseline_status,
        )
        for index, item in enumerate(points)
    ]

    latest = points[-1]
    latest_growth = growth_rates[-1]
    latest_acceleration = accelerations[-1]
    if insufficient:
        trend_state = "insufficient_evidence"
    else:
        trend_state = _classify_state(
            latest_growth,
            latest_acceleration,
            latest.volatility,
            effective_config,
        )
    state_confidence = _state_confidence(
        trend_state,
        latest_growth,
        latest_acceleration,
        latest.volatility,
        latest.window_stability,
        effective_config,
    )
    change_point_confidence = (
        change_points[-1].confidence if change_points else None
    )
    evidence = {
        "window_scores": [
            {
                "window": item.window,
                "score": item.score,
                "duration_days": item.duration_days,
                "source_diversity": item.source_diversity,
                "source_records": list(item.source_records),
                "evidence_ids": list(item.evidence_ids),
                "trend_report_id": item.trend_report_id,
                "analysis_run_id": item.analysis_run_id,
                "source_count": item.source_count,
                "algorithm_version": item.algorithm_version,
                "window_start": item.window_start,
                "window_end": item.window_end,
            }
            for item in ordered
        ],
        "source_diversity": max((item.source_diversity for item in ordered), default=0),
        "source_records": sorted(
            {record for item in ordered for record in item.source_records}
        ),
        "evidence_ids": sorted(
            {evidence_id for item in ordered for evidence_id in item.evidence_ids}
        ),
        "trend_report_ids": sorted(
            {item.trend_report_id for item in ordered if item.trend_report_id}
        ),
        "analysis_run_ids": sorted(
            {item.analysis_run_id for item in ordered if item.analysis_run_id}
        ),
    }
    lineage = {
        "method": TREND_CHANGE_METHOD,
        "algorithm_version": algorithm_version,
        "config_version": config_version,
        "input": "windowed trend scores from existing Trend Signal aggregation",
        "thresholds": dict(effective_config),
    }
    return TrendSubjectAnalysis(
        subject_id=subject_id,
        subject_type=subject_type,
        windows=tuple(points),
        trend_state=trend_state,
        growth_rate=_rounded(latest_growth) if latest_growth is not None else None,
        acceleration=(
            _rounded(latest_acceleration) if latest_acceleration is not None else None
        ),
        absolute_growth=(
            _rounded(float(absolute_growths[-1]))
            if absolute_growths[-1] is not None
            else None
        ),
        relative_growth=(
            _rounded(float(relative_growths[-1]))
            if relative_growths[-1] is not None
            else None
        ),
        window_stability=latest.window_stability,
        volatility=latest.volatility,
        state_confidence=state_confidence,
        change_point_confidence=change_point_confidence,
        confidence=state_confidence,
        change_points=tuple(change_points),
        evidence=evidence,
        lineage=lineage,
        algorithm_version=algorithm_version,
        config_version=config_version,
        config=dict(effective_config),
    )


@dataclass(frozen=True)
class LeadLagResult:
    leading_source: str
    lagging_source: str
    best_lag_windows: int
    correlation: float
    sample_count: int
    interpretation: str = "association_not_causation"
    insufficient_evidence: bool = False


def _pearson_r(x: Sequence[float], y: Sequence[float]) -> float:
    n = len(x)
    mean_x = _mean(x)
    mean_y = _mean(y)
    epsilon = 1e-12
    num = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    denom = (
        sum((xi - mean_x) ** 2 for xi in x) ** 0.5
        * sum((yi - mean_y) ** 2 for yi in y) ** 0.5
    )
    return _rounded(num / max(denom, epsilon))


def source_lead_lag_analysis(
    windows: Sequence[TrendWindowScore],
    *,
    max_lag: int = 3,
    min_samples: int = 5,
    min_correlation: float = 0.3,
) -> list[LeadLagResult]:
    """Analyse cross-source lead/lag relationships from per-window source_scores.

    Returns results sorted by absolute correlation descending.
    Sources with fewer than *min_samples* overlapping windows at all lags are
    excluded (sample_count 0 = unknown).
    """
    ordered = sorted(windows, key=lambda item: item.window)
    source_names = sorted({
        name for item in ordered for name in item.source_scores
    })
    if len(source_names) < 2:
        return []

    results: list[LeadLagResult] = []
    for i, src_a in enumerate(source_names):
        for src_b in source_names[i + 1 :]:
            candidates: list[tuple[float, float, int, int, str, str]] = []
            for lag in range(max_lag + 1):
                if lag == 0:
                    pairs = [
                        index
                        for index, item in enumerate(ordered)
                        if src_a in item.source_scores and src_b in item.source_scores
                    ]
                    if len(pairs) >= min_samples:
                        series_a = [ordered[index].source_scores[src_a] for index in pairs]
                        series_b = [ordered[index].source_scores[src_b] for index in pairs]
                        if max(series_a) != 0.0 or max(series_b) != 0.0:
                            candidates.append(
                                (
                                    abs(_pearson_r(series_a, series_b)),
                                    _pearson_r(series_a, series_b),
                                    lag,
                                    len(pairs),
                                    src_a,
                                    src_b,
                                )
                            )
                    continue

                for leading, lagging in ((src_a, src_b), (src_b, src_a)):
                    pairs = [
                        index
                        for index in range(len(ordered) - lag)
                        if leading in ordered[index].source_scores
                        and lagging in ordered[index + lag].source_scores
                    ]
                    if len(pairs) < min_samples:
                        continue
                    series_a = [ordered[index].source_scores[leading] for index in pairs]
                    series_b = [
                        ordered[index + lag].source_scores[lagging] for index in pairs
                    ]
                    if max(series_a) == 0.0 and max(series_b) == 0.0:
                        continue
                    candidates.append(
                        (
                            abs(_pearson_r(series_a, series_b)),
                            _pearson_r(series_a, series_b),
                            lag,
                            len(pairs),
                            leading,
                            lagging,
                        )
                    )

            if not candidates:
                jointly_observed = sum(
                    1
                    for item in ordered
                    if src_a in item.source_scores and src_b in item.source_scores
                )
                results.append(
                    LeadLagResult(
                        leading_source=src_a,
                        lagging_source=src_b,
                        best_lag_windows=0,
                        correlation=0.0,
                        sample_count=jointly_observed,
                        insufficient_evidence=True,
                    )
                )
                continue

            _, best_r, best_lag, best_n, best_leading, best_lagging = max(
                candidates,
                key=lambda candidate: candidate[0],
            )

            results.append(
                LeadLagResult(
                    leading_source=best_leading,
                    lagging_source=best_lagging,
                    best_lag_windows=best_lag,
                    correlation=best_r,
                    sample_count=best_n,
                )
            )

    results.sort(key=lambda item: abs(item.correlation), reverse=True)
    return [
        r
        for r in results
        if r.insufficient_evidence
        or (abs(r.correlation) >= min_correlation and r.sample_count >= min_samples)
    ]
