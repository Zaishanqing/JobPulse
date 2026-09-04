from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

from app.contexts.review_value_ranking.contracts import (
    DEFAULT_AVAILABLE_SIGNALS,
    ReviewRankInput,
    ReviewRankResult,
)


UNCERTAINTY_SCALE = 10
IMPACT_SCALE = 20
REUSE_SCALE = 20
BLOCKING_WEIGHT = 0.35
UNCERTAINTY_WEIGHT = 0.25
IMPACT_WEIGHT = 0.25
REUSE_WEIGHT = 0.15
FRESHNESS_BONUS_CAP = 7
FRESHNESS_BONUS_WEIGHT = 0.02

# v2: cost-aware utility + MMR diversity.
COST_EXPONENT = 1.0
MMR_LAMBDA = 0.35
# v3: normalized value + structured greedy MMR (no pseudo cost in the main
# formula; cost remains a separate replay axis only).
V3_LAMBDA = 0.6
OBJECT_WEIGHT = 0.30
TYPE_WEIGHT = 0.20
SUBJECT_WEIGHT = 0.15
CANDIDATE_WEIGHT = 0.15
SIGNAL_WEIGHT = 0.20


def rank_review_task(input_: ReviewRankInput) -> ReviewRankResult:
    """Deterministic value-information ranking with decomposed reasons."""
    available = input_.available_signals or DEFAULT_AVAILABLE_SIGNALS
    blocking_score = 1.0 if input_.blocking else 0.0
    uncertainty_score = (
        min(input_.uncertainty_count or 0, UNCERTAINTY_SCALE) / UNCERTAINTY_SCALE
        if "uncertainty" in available
        else 0.0
    )
    impact_score = (
        min(input_.impact_count or 0, IMPACT_SCALE) / IMPACT_SCALE
        if "impact" in available
        else 0.0
    )
    reuse_score = (
        min(input_.reuse_count or 0, REUSE_SCALE) / REUSE_SCALE
        if "reuse" in available
        else 0.0
    )
    freshness = (
        min(max(input_.wait_days or 0.0, 0.0), FRESHNESS_BONUS_CAP)
        / FRESHNESS_BONUS_CAP
        if "freshness" in available
        else 0.0
    )
    score = (
        BLOCKING_WEIGHT * blocking_score
        + UNCERTAINTY_WEIGHT * uncertainty_score
        + IMPACT_WEIGHT * impact_score
        + REUSE_WEIGHT * reuse_score
        + 0.02 * freshness
    )
    reasons: list[str] = []
    if "blocking" in available and input_.blocking:
        reasons.append("blocking_release")
    if "uncertainty" in available and input_.uncertainty_count:
        reasons.append(f"uncertainty:{input_.uncertainty_count}")
    if "impact" in available and input_.impact_count:
        reasons.append(f"impact:{input_.impact_count}")
    if "reuse" in available and input_.reuse_count:
        reasons.append(f"reuse:{input_.reuse_count}")
    if not reasons:
        reasons.append("no_value_signal")
    return ReviewRankResult(
        task_id=input_.task_id,
        priority_score=round(score, 6),
        priority_reasons=tuple(reasons),
        affected_subjects=_affected_subjects(input_),
        blocking_state=bool(input_.blocking),
        similar_task_count=int(input_.reuse_count or 0),
        estimated_review_cost=round(float(input_.estimated_review_cost or 1.0), 3),
    )


def rank_review_task_v2(
    input_: ReviewRankInput,
    *,
    selected: tuple[ReviewRankInput, ...] = (),
    use_cost: bool = True,
    use_mmr: bool = True,
    use_reuse: bool = True,
    use_aging: bool = True,
) -> ReviewRankResult:
    """Cost-aware value-of-information ranking with MMR diversity.

    Utility = VOI / cost^eta; the marginal utility is reduced by the maximum
    similarity to already selected tasks so duplicated high-value tasks do not
    occupy the whole budget.
    """

    base_input = input_
    if not use_reuse or not use_aging:
        base_input = ReviewRankInput(
            task_id=input_.task_id,
            status=input_.status,
            priority=input_.priority,
            blocking=input_.blocking,
            uncertainty_count=input_.uncertainty_count,
            impact_count=input_.impact_count,
            reuse_count=input_.reuse_count if use_reuse else 0,
            wait_days=input_.wait_days if use_aging else 0.0,
            estimated_review_cost=input_.estimated_review_cost,
            created_at=input_.created_at,
            available_signals=input_.available_signals,
            subject_ref=input_.subject_ref,
            entity_ref=input_.entity_ref,
            candidate_ref=input_.candidate_ref,
        )
    base = rank_review_task(base_input)
    cost = 1.0 if not use_cost else max(float(input_.estimated_review_cost or 1.0), 0.05)
    voi = max(base.priority_score, 0.0)
    utility = voi / (cost ** COST_EXPONENT) if use_cost else voi
    similarity = _signal_similarity(input_, selected) if use_mmr else 0.0
    marginal = utility - MMR_LAMBDA * similarity
    reasons = list(base.priority_reasons)
    if selected and similarity > 0:
        reasons.append(f"diversity_penalty:{similarity:.3f}")
    return ReviewRankResult(
        task_id=input_.task_id,
        priority_score=round(marginal, 6),
        priority_reasons=tuple(reasons),
        affected_subjects=base.affected_subjects,
        blocking_state=bool(input_.blocking),
        similar_task_count=int(input_.reuse_count or 0),
        estimated_review_cost=round(cost, 3),
        method_version="review-value-rank.v2",
    )


def rank_review_task_v3(
    input_: ReviewRankInput,
    *,
    selected: tuple[ReviewRankInput, ...] = (),
    lambda_: float = V3_LAMBDA,
) -> ReviewRankResult:
    """Normalized VOI + structured greedy MMR list ranking.

    ``score = lambda * normalized_value + (1 - lambda) * novelty`` where
    novelty is ``1 - max_similarity`` against the already selected tasks.
    Similarity uses real object/type/subject/candidate identity plus the
    deterministic signal vector, so duplicated high-value review tasks do not
    consume the whole budget.
    """

    if not 0 <= lambda_ <= 1:
        raise ValueError("lambda_ must be between 0 and 1")
    base = rank_review_task(input_)
    normalized_value = max(base.priority_score, 0.0) / (
        BLOCKING_WEIGHT
        + UNCERTAINTY_WEIGHT
        + IMPACT_WEIGHT
        + REUSE_WEIGHT
        + FRESHNESS_BONUS_WEIGHT
    )
    normalized_value = min(normalized_value, 1.0)
    similarity = _identity_aware_similarity(input_, selected)
    novelty = 1.0 - similarity
    score = lambda_ * normalized_value + (1.0 - lambda_) * novelty
    reasons = list(base.priority_reasons)
    if selected and similarity > 0:
        reasons.append(f"novelty:{novelty:.3f}")
    return ReviewRankResult(
        task_id=input_.task_id,
        priority_score=round(score, 6),
        priority_reasons=tuple(reasons),
        affected_subjects=base.affected_subjects,
        blocking_state=bool(input_.blocking),
        similar_task_count=int(input_.reuse_count or 0),
        estimated_review_cost=round(float(input_.estimated_review_cost or 1.0), 3),
        method_version="review-value-rank.v3",
    )


def rank_review_task_v4(
    input_: ReviewRankInput,
    *,
    selected: tuple[ReviewRankInput, ...] = (),
    lambda_: float = V3_LAMBDA,
) -> ReviewRankResult:
    """Coverage-gain submodular ranking.

    ``score = lambda * normalized_value + (1-lambda) * diversity_gain`` where
    diversity_gain counts newly covered object types, subjects, candidates and
    uncertainty buckets.  Unlike ``1 - max_similarity``, every covered identity
    contributes an explicit explainable dimension with diminishing returns.
    """

    if not 0 <= lambda_ <= 1:
        raise ValueError("lambda_ must be between 0 and 1")
    base = rank_review_task(input_)
    normalized_value = min(
        max(base.priority_score, 0.0)
        / (
            BLOCKING_WEIGHT
            + UNCERTAINTY_WEIGHT
            + IMPACT_WEIGHT
            + REUSE_WEIGHT
            + FRESHNESS_BONUS_WEIGHT
        ),
        1.0,
    )
    diversity_gain = _coverage_gain(input_, selected)
    score = lambda_ * normalized_value + (1.0 - lambda_) * diversity_gain
    reasons = list(base.priority_reasons)
    if selected and diversity_gain > 0:
        reasons.append(f"coverage_gain:{diversity_gain:.3f}")
    return ReviewRankResult(
        task_id=input_.task_id,
        priority_score=round(score, 6),
        priority_reasons=tuple(reasons),
        affected_subjects=base.affected_subjects,
        blocking_state=bool(input_.blocking),
        similar_task_count=int(input_.reuse_count or 0),
        estimated_review_cost=round(
            float(input_.estimated_review_cost or 1.0), 3
        ),
        method_version="review-value-rank.v4",
    )


def rank_review_queue_v4(
    tasks: Sequence[ReviewRankInput],
    *,
    lambda_: float = V3_LAMBDA,
) -> tuple[ReviewRankInput, ...]:
    """Greedy listwise v4 ranking over the whole queue."""

    remaining = list(tasks)
    selected: list[ReviewRankInput] = []
    while remaining:
        best_index, _best_score = max(
            (
                (
                    index,
                    rank_review_task_v4(
                        task,
                        selected=tuple(selected),
                        lambda_=lambda_,
                    ).priority_score,
                )
                for index, task in enumerate(remaining)
            ),
            key=lambda pair: pair[1],
        )
        selected.append(remaining.pop(best_index))
    return tuple(selected)


def rank_review_task_v5(
    input_: ReviewRankInput,
    *,
    selected: tuple[ReviewRankInput, ...] = (),
    lambda_: float = 0.4,
    eta: float = 1.0,
    p_change: float | None = None,
    reviewed_in_group: int = 0,
    changed_in_group: int = 0,
) -> ReviewRankResult:
    """Expected Review Value with reuse-group diminishing returns."""

    base = rank_review_task(input_)
    propagation_count = (
        input_.propagation_count
        or input_.reuse_group_size
        or int(input_.reuse_count or 0)
    )
    remaining = max(propagation_count - reviewed_in_group, 0)
    propagation = (
        math.log1p(remaining) / math.log1p(max(propagation_count, 1))
        if propagation_count
        else (1.0 if remaining > 0 else 0.0)
    )
    probability = (
        p_change
        if p_change is not None
        else _beta_posterior_p_change(
            reviewed_in_group, changed_in_group
        )
    )
    blocking_value = 1.0 if input_.blocking else 0.0
    downstream_value = min((input_.impact_count or 0) / 10.0, 1.0)
    expected_value = probability * (
        0.4 * blocking_value
        + 0.3 * downstream_value
        + 0.3 * propagation
    )
    cost = max(float(input_.estimated_review_cost or 1.0), 0.05)
    value = expected_value / (cost ** eta)
    diversity_gain = _coverage_gain(input_, selected)
    aging = min(max(input_.wait_days or 0.0, 0.0), 30.0) / 30.0
    score = value + lambda_ * diversity_gain + 0.02 * aging
    reasons = list(base.priority_reasons)
    reasons.append(f"p_change:{probability:.3f}")
    if propagation:
        reasons.append(f"reuse_group_remaining:{remaining}")
    if diversity_gain > 0:
        reasons.append(f"coverage_gain:{diversity_gain:.3f}")
    return ReviewRankResult(
        task_id=input_.task_id,
        priority_score=round(score, 6),
        priority_reasons=tuple(reasons),
        affected_subjects=base.affected_subjects,
        blocking_state=bool(input_.blocking),
        similar_task_count=int(input_.reuse_count or 0),
        estimated_review_cost=round(cost, 3),
        method_version="review-value-rank.v5",
    )


def rank_review_queue_v5(
    tasks: Sequence[ReviewRankInput],
    *,
    p_change_by_id: Mapping[str, float] | None = None,
    lambda_: float = 0.4,
) -> tuple[ReviewRankInput, ...]:
    """Blocking-first greedy queue with group-aware diminishing returns.

    ``p_change`` is frozen for the whole ranking pass: it may only come from
    pre-ranking historical data or the global prior, never from tasks being
    selected into the queue.
    """

    p_change_by_id = p_change_by_id or {}
    frozen_p_change = {
        task.task_id: float(p_change_by_id[task.task_id])
        if task.task_id in p_change_by_id
        else _beta_posterior_p_change(0, 0)
        for task in tasks
    }
    reviewed_in_group: dict[str, int] = {}

    def group_ref(task: ReviewRankInput) -> str:
        return task.reuse_group_ref or _resolved_group_ref(task)

    blocking = sorted(
        (task for task in tasks if task.blocking),
        key=lambda task: (
            -rank_review_task(task).priority_score,
            task.task_id,
        ),
    )
    remaining = [
        task for task in tasks if not task.blocking
    ]
    selected: list[ReviewRankInput] = []
    for task in blocking:
        selected.append(task)
        key = group_ref(task)
        reviewed_in_group[key] = reviewed_in_group.get(key, 0) + 1
    while remaining:
        best_index = max(
            range(len(remaining)),
            key=lambda index: (
                rank_review_task_v5(
                    remaining[index],
                    selected=tuple(selected),
                    lambda_=lambda_,
                    p_change=frozen_p_change[remaining[index].task_id],
                    reviewed_in_group=reviewed_in_group.get(
                        group_ref(remaining[index]), 0
                    ),
                    changed_in_group=0,
                ).priority_score,
                remaining[index].task_id,
            ),
        )
        chosen = remaining.pop(best_index)
        selected.append(chosen)
        key = group_ref(chosen)
        reviewed_in_group[key] = reviewed_in_group.get(key, 0) + 1
    return tuple(selected)


def rank_review_task_v6(
    input_: ReviewRankInput,
    *,
    selected: tuple[ReviewRankInput, ...] = (),
    lambda_: float = V3_LAMBDA,
    redundancy_mu: float = 0.0,
    aging_weight: float = 0.0,
    propagation_weight: float = 0.0,
    propagation_cap: int = 10,
) -> ReviewRankResult:
    """v4 base with capped same-group redundancy and small aging/propagation.

    Designed to keep high-value tasks first; only a bounded penalty for
    already-selected same-group tasks, instead of a large additive diversity
    reward.
    """

    if redundancy_mu < 0 or aging_weight < 0 or propagation_weight < 0:
        raise ValueError("v6 weights must be non-negative")
    if propagation_cap < 1:
        raise ValueError("propagation_cap must be positive")
    base = rank_review_task_v4(input_, selected=selected, lambda_=lambda_)
    aging = min(max(input_.wait_days or 0.0, 0.0), 30.0) / 30.0
    group_key = _resolved_group_ref(input_)
    selected_same = sum(
        1 for item in selected if _resolved_group_ref(item) == group_key
    )
    group_size = max(int(input_.reuse_group_size or 1), 1)
    remaining = max(group_size - selected_same, 0)
    propagation = min(remaining, propagation_cap) / propagation_cap
    score = (
        base.priority_score
        + aging_weight * aging
        + propagation_weight * propagation
        - redundancy_mu * math.log1p(selected_same)
    )
    reasons = list(base.priority_reasons)
    if selected_same:
        reasons.append(f"same_group_selected:{selected_same}")
    if propagation_weight and propagation > 0:
        reasons.append(f"propagation_ratio:{propagation:.3f}")
    return ReviewRankResult(
        task_id=input_.task_id,
        priority_score=round(score, 6),
        priority_reasons=tuple(reasons),
        affected_subjects=base.affected_subjects,
        blocking_state=bool(input_.blocking),
        similar_task_count=int(input_.reuse_count or 0),
        estimated_review_cost=1.0,
        method_version="review-value-rank.v6",
    )


def rank_review_queue_v6(
    tasks: Sequence[ReviewRankInput],
    *,
    lambda_: float = V3_LAMBDA,
    redundancy_mu: float = 0.0,
    aging_weight: float = 0.0,
    propagation_weight: float = 0.0,
    propagation_cap: int = 10,
) -> tuple[ReviewRankInput, ...]:
    """Tier-0 blocking, then greedy v4-based score with group penalty."""

    blocking = sorted(
        (task for task in tasks if task.blocking),
        key=lambda task: (
            -rank_review_task_v4(task, lambda_=lambda_).priority_score,
            task.task_id,
        ),
    )
    remaining = [task for task in tasks if not task.blocking]
    selected: list[ReviewRankInput] = list(blocking)
    while remaining:
        best_index = max(
            range(len(remaining)),
            key=lambda index: (
                rank_review_task_v6(
                    remaining[index],
                    selected=tuple(selected),
                    lambda_=lambda_,
                    redundancy_mu=redundancy_mu,
                    aging_weight=aging_weight,
                    propagation_weight=propagation_weight,
                    propagation_cap=propagation_cap,
                ).priority_score,
                remaining[index].task_id,
            ),
        )
        selected.append(remaining.pop(best_index))
    return tuple(selected)


def _resolved_group_ref(task: ReviewRankInput) -> str:
    obj_type = task.object_type or ""
    ent = task.entity_ref or task.object_id or task.task_id
    return f"{obj_type}|{ent}"


def _laplace_p_change(
    group_size: int,
    changed: int = 0,
    *,
    alpha: float = 1.0,
    beta: float = 2.0,
) -> float:
    return round(
        (changed + alpha) / (group_size + alpha + beta),
        6,
    )


def _beta_posterior_p_change(
    reviewed_count: int,
    changed_count: int,
    *,
    prior: float = 0.31,
    strength: float = 2.0,
) -> float:
    """Beta posterior; with no reviews it returns the global prior."""

    if reviewed_count <= 0:
        return round(prior, 6)
    alpha = max(prior * strength, 1e-6)
    beta = max((1.0 - prior) * strength, 1e-6)
    return round(
        (changed_count + alpha) / (reviewed_count + alpha + beta),
        6,
    )


def _coverage_gain(
    task: ReviewRankInput,
    selected: tuple[ReviewRankInput, ...],
) -> float:
    covered_objects = {
        item.object_ref for item in selected if item.object_ref
    }
    covered_types = {item.object_type for item in selected if item.object_type}
    covered_subjects = {
        item.subject_ref for item in selected if item.subject_ref
    }
    covered_candidates = {
        item.candidate_ref for item in selected if item.candidate_ref
    }
    covered_buckets = {
        _uncertainty_bucket(item.uncertainty_count)
        for item in selected
    }
    new_object = (
        1.0
        if task.object_ref and task.object_ref not in covered_objects
        else 0.0
    )
    new_type = (
        1.0
        if task.object_type and task.object_type not in covered_types
        else 0.0
    )
    new_subject = (
        1.0
        if task.subject_ref and task.subject_ref not in covered_subjects
        else 0.0
    )
    new_candidate = (
        1.0
        if task.candidate_ref and task.candidate_ref not in covered_candidates
        else 0.0
    )
    new_bucket = (
        1.0
        if _uncertainty_bucket(task.uncertainty_count)
        not in covered_buckets
        else 0.0
    )
    return round(
        OBJECT_WEIGHT * new_object
        + TYPE_WEIGHT * new_type
        + SUBJECT_WEIGHT * new_subject
        + CANDIDATE_WEIGHT * new_candidate
        + SIGNAL_WEIGHT * new_bucket,
        6,
    )


def _uncertainty_bucket(uncertainty_count: int | None) -> int:
    return int((uncertainty_count or 0) // 5)


def _identity_aware_similarity(
    task: ReviewRankInput,
    selected: tuple[ReviewRankInput, ...],
) -> float:
    if not selected:
        return 0.0
    features = _feature_vector(task)
    best = 0.0
    for other in selected:
        denominator = max(
            sum(features) + sum(_feature_vector(other)) - _dot(features, _feature_vector(other)),
            1.0,
        )
        signal = _dot(features, _feature_vector(other)) / denominator
        identity = (
            OBJECT_WEIGHT * _same_ref(task.object_ref, other.object_ref)
            + TYPE_WEIGHT * _same_ref(task.object_type, other.object_type)
            + SUBJECT_WEIGHT * _same_ref(task.subject_ref, other.subject_ref)
            + CANDIDATE_WEIGHT
            * _same_ref(task.candidate_ref, other.candidate_ref)
            + SIGNAL_WEIGHT * signal
        )
        best = max(best, identity)
    return round(min(best, 1.0), 6)


def _same_ref(left: str | None, right: str | None) -> float:
    if not left or not right:
        return 0.0
    return 1.0 if left == right else 0.0


def _signal_similarity(
    task: ReviewRankInput, selected: tuple[ReviewRankInput, ...]
) -> float:
    if not selected:
        return 0.0
    features = _feature_vector(task)
    best = 0.0
    for other in selected:
        other_features = _feature_vector(other)
        denominator = max(
            sum(features) + sum(other_features) - _dot(features, other_features),
            1.0,
        )
        best = max(best, _dot(features, other_features) / denominator)
    return round(best, 6)


def _feature_vector(task: ReviewRankInput) -> tuple[float, float, float, float]:
    return (
        1.0 if task.blocking else 0.0,
        float(min(task.uncertainty_count or 0, UNCERTAINTY_SCALE) / UNCERTAINTY_SCALE),
        float(min(task.impact_count or 0, IMPACT_SCALE) / IMPACT_SCALE),
        float(min(task.reuse_count or 0, REUSE_SCALE) / REUSE_SCALE),
    )


def _dot(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def review_wait_days(
    created_at: datetime | str | None, now: datetime | None = None
) -> float:
    if created_at is None:
        return 0.0
    if isinstance(created_at, str):
        try:
            created_at = datetime.fromisoformat(created_at)
        except ValueError:
            return 0.0
    reference = now or datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return max((reference - created_at).total_seconds() / 86400.0, 0.0)


def _affected_subjects(input_: ReviewRankInput) -> tuple[str, ...]:
    subjects: list[str] = []
    if input_.subject_ref:
        subjects.append(str(input_.subject_ref))
    if input_.entity_ref:
        subjects.append(str(input_.entity_ref))
    if input_.candidate_ref:
        subjects.append(str(input_.candidate_ref))
    if subjects:
        return tuple(subjects)
    if input_.impact_count:
        subjects.append(f"{input_.impact_count}_subjects")
    if input_.uncertainty_count:
        subjects.append(f"{input_.uncertainty_count}_uncertain_items")
    return tuple(subjects)


__all__ = [
    "BLOCKING_WEIGHT",
    "FRESHNESS_BONUS_WEIGHT",
    "FRESHNESS_BONUS_CAP",
    "IMPACT_SCALE",
    "IMPACT_WEIGHT",
    "REUSE_SCALE",
    "REUSE_WEIGHT",
    "UNCERTAINTY_SCALE",
    "UNCERTAINTY_WEIGHT",
    "rank_review_task",
    "rank_review_task_v2",
    "rank_review_task_v3",
    "rank_review_task_v4",
    "rank_review_queue_v4",
    "rank_review_task_v5",
    "rank_review_queue_v5",
    "rank_review_queue_v6",
    "rank_review_task_v6",
    "review_wait_days",
]
