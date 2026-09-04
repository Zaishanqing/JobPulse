from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import sqrt


CONFIG_TYPES = frozenset({
    "job_knowledge",
    "policy_keywords",
    "domain_dictionary",
    "github_topics",
    "trend_thresholds",
})


def ranking_metrics(
    predictions: Sequence[Mapping[str, object]],
    ground_truth: Sequence[Mapping[str, object]],
    *,
    k: int,
) -> dict[str, float]:
    predicted = [str(item["candidate_key"]) for item in predictions[:k]]
    actual = [str(item["candidate_key"]) for item in ground_truth]
    hits = len(set(predicted) & set(actual))
    precision = hits / k if k else 0.0
    recall = hits / len(set(actual)) if actual else 0.0
    actual_rank = {key: index + 1 for index, key in enumerate(actual)}
    common = [key for key in predicted if key in actual_rank]
    if len(common) < 2:
        correlation = 0.0
    else:
        x = list(range(1, len(common) + 1))
        y = [actual_rank[key] for key in common]
        mean_x, mean_y = sum(x) / len(x), sum(y) / len(y)
        numerator = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y))
        denominator = sqrt(
            sum((a - mean_x) ** 2 for a in x) * sum((b - mean_y) ** 2 for b in y)
        )
        correlation = numerator / denominator if denominator else 0.0
    expected_directions = {
        str(item["candidate_key"]): str(item.get("direction", "stable"))
        for item in ground_truth
    }
    comparable = [item for item in predictions if str(item["candidate_key"]) in expected_directions]
    correct = sum(
        str(item.get("direction", "stable")) == expected_directions[str(item["candidate_key"])]
        for item in comparable
    )
    return {
        f"precision_at_{k}": round(precision, 6),
        f"recall_at_{k}": round(recall, 6),
        "ranking_correlation": round(correlation, 6),
        "direction_accuracy": round(correct / len(comparable), 6) if comparable else 0.0,
    }


def quality_flags(
    *,
    evidence_count: int,
    source_contributions: Mapping[str, float],
    evidence_age_days: float | None,
    growth_rate: float | None,
    thresholds: Mapping[str, object],
) -> list[str]:
    flags: list[str] = []
    if evidence_count < int(thresholds.get("low_sample_count", 3)):
        flags.append("low_sample")
    absolute = {key: abs(float(value)) for key, value in source_contributions.items()}
    total = sum(absolute.values())
    if total and max(absolute.values()) / total >= float(thresholds.get("single_source_dominance", 0.8)):
        flags.append("single_source_dominance")
    if evidence_age_days is not None and evidence_age_days > float(thresholds.get("stale_evidence_days", 180)):
        flags.append("stale_evidence")
    values = [float(value) for value in source_contributions.values() if value]
    if values and min(values) < 0 < max(values):
        flags.append("source_conflict")
    return flags
