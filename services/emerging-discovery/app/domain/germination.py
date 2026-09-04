"""Deterministic, decomposable emerging-position ranking index."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class GerminationDimensions:
    cluster_growth_rate: float
    skill_combo_novelty: float
    source_diversity: float
    industry_spread: float
    distance_from_existing_positions: float
    sample_size_penalty: float
    single_platform_noise_penalty: float
    duplicate_sample_penalty: float


@dataclass(frozen=True)
class GerminationAssessmentResult:
    germination_score: float
    dimensions: GerminationDimensions
    level: str
    qualified_as_emerging: bool
    decision_reason: str
    weights: Mapping[str, float]
    thresholds: Mapping[str, float | int]
    evidence_summary: Mapping[str, object]
    formula_version: str


DEFAULT_GERMINATION_CONFIG: dict[str, object] = {
    "growth_weight": 0.18,
    "persistence_weight": 0.16,
    "enterprise_coverage_weight": 0.12,
    "source_diversity_weight": 0.12,
    "standard_position_distance_weight": 0.18,
    "evidence_quality_weight": 0.12,
    "result_stability_weight": 0.12,
    "minimum_sample_size": 3,
    "minimum_source_count": 2,
    "target_enterprise_count": 3,
    "target_source_count": 3,
    "target_industry_count": 3,
    "emerging_threshold": 0.60,
    "high_potential_threshold": 0.75,
    "minimum_stability_score": 0.65,
    "formula_version": "emergence-index-v4-seven-dimensions",
}


def bounded(value: float) -> float:
    return min(max(float(value), 0.0), 1.0)


def formal_reference_distance(
    candidate_skills: set[str], reference_skill_sets: list[set[str]]
) -> tuple[float, dict[str, object]]:
    if not candidate_skills:
        raise ValueError("position distance cannot be evaluated without candidate skills")
    references = [value for value in reference_skill_sets if value]
    if not references:
        raise ValueError("position distance cannot be evaluated without formal references")
    similarities = []
    for reference in references:
        union = candidate_skills | reference
        similarities.append(len(candidate_skills & reference) / len(union) if union else 0.0)
    maximum = max(similarities)
    return bounded(1.0 - maximum), {
        "method": "nearest_standard_position_skill_jaccard_v2",
        "candidate_skills": sorted(candidate_skills),
        "reference_count": len(references),
        "maximum_skill_similarity": round(maximum, 6),
        "responsibility_similarity": "unavailable",
        "semantic_similarity": "unavailable",
        "business_meaning": "higher values mean a larger skill-combination gap from the nearest standard position",
    }


def _window_growth(
    cluster_windows: list[str], all_windows: list[str], required_windows: list[str] | None = None
) -> tuple[float, dict[str, object]]:
    ordered = list(dict.fromkeys(required_windows or all_windows))
    if len(ordered) < 3:
        raise ValueError("growth requires at least three ordered historical windows")
    counts = Counter(cluster_windows)
    totals = Counter(all_windows)
    cluster_counts = [counts[item] for item in ordered]
    total_counts = [totals[item] for item in ordered]
    shares = [
        cluster_count / total_count if total_count else 0.0
        for cluster_count, total_count in zip(cluster_counts, total_counts, strict=True)
    ]
    relative_change = (shares[-1] - shares[0]) / max(shares[0], 1 / max(total_counts[0], 1))
    normalized = bounded(0.5 + 0.5 * math.tanh(relative_change))
    return normalized, {
        "method": "three_window_cluster_share_growth_v2",
        "windows": tuple(
            {
                "window_id": window,
                "cluster_jd_count": cluster_counts[index],
                "all_jd_count": total_counts[index],
                "cluster_share": round(shares[index], 6),
            }
            for index, window in enumerate(ordered)
        ),
        "raw_relative_change": round(relative_change, 6),
        "business_meaning": "measures change in the cluster share of valid JDs across the first and last windows",
    }


def assess_germination(
    *,
    sample_count: int,
    effective_sample_count: int,
    sources: list[str],
    spread_labels: list[str],
    publish_dates: list[date],
    all_publish_dates: list[date],
    candidate_skills: set[str],
    reference_skill_sets: list[set[str]],
    stability_score: float,
    config: Mapping[str, object],
    enterprises: list[str | None] | None = None,
    window_ids: list[str] | None = None,
    all_window_ids: list[str] | None = None,
    evidence_quality: Mapping[str, object] | None = None,
    required_window_ids: list[str] | None = None,
) -> GerminationAssessmentResult:
    merged = {**DEFAULT_GERMINATION_CONFIG, **config}
    enterprises = enterprises or [None] * sample_count
    cluster_windows = window_ids or [value.strftime("%Y-%m") for value in publish_dates]
    observed_windows = all_window_ids or [value.strftime("%Y-%m") for value in all_publish_dates]
    growth, growth_evidence = _window_growth(
        cluster_windows, observed_windows, required_window_ids
    )
    ordered_windows = list(dict.fromkeys(required_window_ids or observed_windows))
    occupied = len(set(cluster_windows) & set(ordered_windows))
    persistence = bounded(occupied / len(ordered_windows))

    duplicate_guard = bounded(effective_sample_count / max(sample_count, 1))
    known_enterprises = {value for value in enterprises if value}
    enterprise_coverage = (
        bounded(len(known_enterprises) / max(int(merged["target_enterprise_count"]), 1))
        * duplicate_guard
    )
    unique_sources = {value for value in sources if value and value != "unknown"}
    source_diversity = (
        bounded(len(unique_sources) / max(int(merged["target_source_count"]), 1)) * duplicate_guard
    )
    distance, distance_evidence = formal_reference_distance(candidate_skills, reference_skill_sets)

    required_quality = (
        "evidence_count_score",
        "field_coverage",
        "source_reliability",
        "original_text_locatability",
    )
    quality_components = dict(evidence_quality or {})
    quality_available = bool(evidence_quality) and all(
        name in quality_components for name in required_quality
    )
    if not quality_available:
        quality_components = {
            name: quality_components.get(name, 0.0) for name in required_quality
        }
        quality_components["status"] = "unknown"
    evidence_quality_value = sum(
        bounded(float(quality_components.get(name, 0.0))) for name in required_quality
    ) / len(required_quality)
    stability = bounded(stability_score)

    values = {
        "growth": growth,
        "cross_window_persistence": persistence,
        "enterprise_coverage": enterprise_coverage,
        "source_diversity": source_diversity,
        "standard_position_distance": distance,
        "evidence_quality": evidence_quality_value,
        "result_stability": stability,
    }
    weights = {
        name: float(merged[f"{name.replace('cross_window_', '')}_weight"]) for name in values
    }
    if abs(sum(weights.values()) - 1.0) > 1e-9 or any(value < 0 for value in weights.values()):
        raise ValueError("emergence index weights must be non-negative and sum to one")
    raw = {
        "growth": growth_evidence,
        "cross_window_persistence": {
            "occupied_window_count": occupied,
            "total_window_count": len(ordered_windows),
            "window_ids": tuple(ordered_windows),
        },
        "enterprise_coverage": {
            "unique_enterprise_count": len(known_enterprises),
            "sample_count": sample_count,
            "effective_sample_count": effective_sample_count,
            "duplicate_guard": round(duplicate_guard, 6),
        },
        "source_diversity": {
            "unique_source_count": len(unique_sources),
            "sources": tuple(sorted(unique_sources)),
            "duplicate_guard": round(duplicate_guard, 6),
        },
        "standard_position_distance": distance_evidence,
        "evidence_quality": quality_components,
        "result_stability": {
            "robust_membership_ratio": stability,
            "method": "accepted-edge-threshold-perturbation-v1",
        },
    }
    meanings = {
        "growth": "valid JD share growth across historical windows",
        "cross_window_persistence": "how many required windows contain real cluster members",
        "enterprise_coverage": "unique enterprise coverage after duplicate suppression",
        "source_diversity": "independent source coverage after duplicate suppression",
        "standard_position_distance": "difference from the nearest formal position",
        "evidence_quality": "evidence count, field coverage, source reliability and text locatability",
        "result_stability": "membership robustness under a +0.05 candidate-threshold perturbation",
    }
    breakdown = {
        name: {
            "raw_value": raw[name],
            "normalized_value": round(value, 6),
            "weight": weights[name],
            "contribution": round(value * weights[name], 6),
            "business_meaning": meanings[name],
        }
        for name, value in values.items()
    }
    # 唯一权威评分成分：与 emergence_index.dimensions 完全一致，
    # 仅包含正式七维加权维度；诊断特征（距离/新颖度/legacy 字段）不在此处重复计分。
    score_components = [
        {
            "name": name,
            **breakdown[name],
        }
        for name, value in values.items()
    ]
    score = round(sum(values[name] * weights[name] for name in values), 6)
    threshold = float(merged["emerging_threshold"])
    qualified = (
        score >= threshold
        and effective_sample_count >= int(merged["minimum_sample_size"])
        and len(unique_sources) >= int(merged["minimum_source_count"])
        and stability >= float(merged["minimum_stability_score"])
        and occupied >= 3
        and quality_available
    )
    high = float(merged["high_potential_threshold"])
    level = (
        "high_potential"
        if qualified and score >= high
        else "emerging"
        if qualified
        else "watchlist"
    )
    dimensions = GerminationDimensions(
        cluster_growth_rate=round(growth, 4),
        skill_combo_novelty=round(distance, 4),
        source_diversity=round(source_diversity, 4),
        industry_spread=round(
            bounded(len(set(spread_labels)) / max(int(merged["target_industry_count"]), 1)),
            4,
        ),
        distance_from_existing_positions=round(distance, 4),
        sample_size_penalty=0.0
        if effective_sample_count >= int(merged["minimum_sample_size"])
        else -0.15,
        single_platform_noise_penalty=0.0 if len(unique_sources) > 1 else -0.08,
        duplicate_sample_penalty=round(-(1.0 - duplicate_guard) * 0.2, 4),
    )
    # 诊断特征：保留 closest/nearest 标准岗位、技能新颖度、legacy dimension_inputs、
    # 原始 penalty 等供排查与解释使用；全部明确 not_scored，不参与二次加权。
    diagnostic_features = {
        "standard_position_distance": {
            **distance_evidence,
            "scored": False,
            "note": "diagnostic only; the formal score uses standard_position_distance once",
        },
        "skill_novelty_diagnostic": {
            "value": round(distance, 4),
            "scored": False,
            "note": "skill novelty is a diagnostic derived from the same distance evidence; it is not an independent score dimension",
        },
        "legacy_dimension_inputs": {
            **vars(dimensions),
            "scored": False,
            "note": "deprecated legacy dimensions kept for compatibility; not part of the formal 7-dim score",
        },
        "penalty_diagnostics": {
            "sample_size_penalty": dimensions.sample_size_penalty,
            "single_platform_noise_penalty": dimensions.single_platform_noise_penalty,
            "duplicate_sample_penalty": dimensions.duplicate_sample_penalty,
            "scored": False,
        },
    }
    return GerminationAssessmentResult(
        germination_score=score,
        dimensions=dimensions,
        level=level,
        qualified_as_emerging=qualified,
        decision_reason=(
            "ranking index and independent publication-quality prerequisites are satisfied"
            if qualified
            else "candidate ranking index or one of the independent quality prerequisites is not satisfied"
        ),
        weights=weights,
        thresholds={
            "emerging": threshold,
            "high_potential": high,
            "minimum_sample_size": int(merged["minimum_sample_size"]),
            "minimum_source_count": int(merged["minimum_source_count"]),
            "minimum_stability_score": float(merged["minimum_stability_score"]),
            "minimum_window_count": 3,
        },
        evidence_summary={
            "score_semantics": "composite ranking index, not an occurrence probability",
            "sample_count": sample_count,
            "effective_sample_count": effective_sample_count,
            "duplicate_sample_count": sample_count - effective_sample_count,
            "source_count": len(unique_sources),
            "evidence_quality": "unknown" if not quality_available else quality_components,
            "sources": sorted(unique_sources),
            "enterprise_count": len(known_enterprises),
            "enterprises": sorted(known_enterprises),
            "publish_date_start": min(publish_dates).isoformat() if publish_dates else None,
            "publish_date_end": max(publish_dates).isoformat() if publish_dates else None,
            "growth": growth_evidence,
            "position_reference_distance": distance_evidence,
            "emergence_index": {
                "name": "emergence_score",
                "semantics": "composite ranking index, not a probability",
                "formula_version": str(merged["formula_version"]),
                "dimensions": breakdown,
                "total_score": score,
            },
            "score_components": score_components,
            "diagnostic_features": diagnostic_features,
        },
        formula_version=str(merged["formula_version"]),
    )
