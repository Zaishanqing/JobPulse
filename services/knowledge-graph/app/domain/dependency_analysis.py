"""Statistical skill-dependency candidates built from requirement contexts.

Edges produced here are analytical candidates.  The direction ``a -> b`` means
that observing ``b`` more strongly implies observing ``a`` than the reverse; it
does not express causality or a teaching prerequisite.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
import random


@dataclass(frozen=True)
class RequirementContext:
    context_id: str
    document_id: str
    requirement_id: str
    skill_ids: frozenset[str]
    source_name: str
    enterprise_id: str
    industry: str
    region: str
    time_slice: str
    template_family_id: str
    evidence_ids: tuple[int, ...]


@dataclass(frozen=True)
class DependencyPolicy:
    minimum_joint_support: int
    minimum_conditional_probability: float
    minimum_source_diversity: int
    minimum_enterprise_diversity: int
    maximum_enterprise_share: float
    bootstrap_iterations: int
    confidence_level: float
    minimum_stable_slices: int


@dataclass(frozen=True)
class ExcludedRequirementContext:
    context_id: str
    reason: str


@dataclass(frozen=True)
class DependencyCandidate:
    prerequisite_skill_id: str
    advanced_skill_id: str
    dependency_score: float
    probability_prerequisite_given_advanced: float
    probability_advanced_given_prerequisite: float
    joint_support: int
    source_diversity: int
    enterprise_diversity: int
    maximum_enterprise_share: float
    bootstrap_lower: float
    bootstrap_upper: float
    stable_slices: tuple[str, ...]
    evidence_ids: tuple[int, ...]
    claim_kind: str = "inferred_candidate"


@dataclass(frozen=True)
class RejectedDependencyCandidate:
    prerequisite_skill_id: str
    advanced_skill_id: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class DependencyAnalysis:
    included_contexts: tuple[RequirementContext, ...]
    excluded_contexts: tuple[ExcludedRequirementContext, ...]
    candidates: tuple[DependencyCandidate, ...]
    rejected: tuple[RejectedDependencyCandidate, ...]


def _validate_policy(policy: DependencyPolicy) -> None:
    if policy.minimum_joint_support < 1:
        raise ValueError("minimum_joint_support must be positive")
    if policy.minimum_source_diversity < 1 or policy.minimum_enterprise_diversity < 1:
        raise ValueError("diversity thresholds must be positive")
    if not 0 < policy.minimum_conditional_probability <= 1:
        raise ValueError("minimum_conditional_probability must be within (0, 1]")
    if not 0 < policy.maximum_enterprise_share <= 1:
        raise ValueError("maximum_enterprise_share must be within (0, 1]")
    if policy.bootstrap_iterations < 100:
        raise ValueError("bootstrap_iterations must be at least 100")
    if not 0 < policy.confidence_level < 1:
        raise ValueError("confidence_level must be within (0, 1)")
    if policy.minimum_stable_slices < 1:
        raise ValueError("minimum_stable_slices must be positive")


def _validate_context(context: RequirementContext) -> None:
    required = (
        context.context_id,
        context.document_id,
        context.requirement_id,
        context.source_name,
        context.enterprise_id,
        context.industry,
        context.region,
        context.time_slice,
        context.template_family_id,
    )
    if any(not value.strip() for value in required):
        raise ValueError("requirement context fields cannot be empty")
    if len(context.skill_ids) < 2:
        raise ValueError("requirement context requires at least two distinct skills")
    if not context.evidence_ids or any(value <= 0 for value in context.evidence_ids):
        raise ValueError("requirement context requires valid evidence IDs")


def _deduplicate_contexts(
    contexts: tuple[RequirementContext, ...],
) -> tuple[tuple[RequirementContext, ...], tuple[ExcludedRequirementContext, ...]]:
    selected: dict[tuple[str, str, frozenset[str]], RequirementContext] = {}
    excluded: list[ExcludedRequirementContext] = []
    for context in sorted(contexts, key=lambda item: item.context_id):
        _validate_context(context)
        key = (context.enterprise_id, context.template_family_id, context.skill_ids)
        if key in selected:
            excluded.append(
                ExcludedRequirementContext(context.context_id, "duplicate_enterprise_template")
            )
            continue
        selected[key] = context
    return tuple(selected.values()), tuple(excluded)


def _dependency_score(
    contexts: tuple[RequirementContext, ...], prerequisite: str, advanced: str
) -> tuple[float, float, float, int]:
    prerequisite_count = sum(prerequisite in item.skill_ids for item in contexts)
    advanced_count = sum(advanced in item.skill_ids for item in contexts)
    joint = sum(
        prerequisite in item.skill_ids and advanced in item.skill_ids for item in contexts
    )
    if prerequisite_count == 0 or advanced_count == 0:
        return 0.0, 0.0, 0.0, 0
    p_prerequisite_given_advanced = joint / advanced_count
    p_advanced_given_prerequisite = joint / prerequisite_count
    return (
        p_prerequisite_given_advanced - p_advanced_given_prerequisite,
        p_prerequisite_given_advanced,
        p_advanced_given_prerequisite,
        joint,
    )


def _percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return ordered[lower_index]
    fraction = position - lower_index
    return ordered[lower_index] * (1 - fraction) + ordered[upper_index] * fraction


def _bootstrap_interval(
    contexts: tuple[RequirementContext, ...],
    prerequisite: str,
    advanced: str,
    policy: DependencyPolicy,
) -> tuple[float, float]:
    generator = random.Random(0)
    samples: list[float] = []
    for _ in range(policy.bootstrap_iterations):
        resampled = tuple(generator.choice(contexts) for _ in contexts)
        score, _, _, _ = _dependency_score(resampled, prerequisite, advanced)
        samples.append(score)
    alpha = (1 - policy.confidence_level) / 2
    return _percentile(samples, alpha), _percentile(samples, 1 - alpha)


def analyze_skill_dependencies(
    contexts: tuple[RequirementContext, ...], policy: DependencyPolicy
) -> DependencyAnalysis:
    _validate_policy(policy)
    included, excluded = _deduplicate_contexts(contexts)
    if not included:
        raise ValueError("dependency analysis requires requirement contexts")
    skills = sorted({skill for context in included for skill in context.skill_ids})
    accepted: list[DependencyCandidate] = []
    rejected: list[RejectedDependencyCandidate] = []
    for prerequisite in skills:
        for advanced in skills:
            if prerequisite == advanced:
                continue
            score, p_given, reverse_p, joint = _dependency_score(
                included, prerequisite, advanced
            )
            if score <= 0:
                continue
            supporting = tuple(
                item
                for item in included
                if prerequisite in item.skill_ids and advanced in item.skill_ids
            )
            sources = {item.source_name for item in supporting}
            enterprises = Counter(item.enterprise_id for item in supporting)
            maximum_share = max(enterprises.values(), default=0) / max(joint, 1)
            lower, upper = _bootstrap_interval(
                included, prerequisite, advanced, policy
            )
            slices = sorted({item.time_slice for item in included})
            stable_slices = tuple(
                time_slice
                for time_slice in slices
                if _dependency_score(
                    tuple(item for item in included if item.time_slice == time_slice),
                    prerequisite,
                    advanced,
                )[0]
                > 0
            )
            reasons: list[str] = []
            if joint < policy.minimum_joint_support:
                reasons.append("joint_support_below_threshold")
            if p_given < policy.minimum_conditional_probability:
                reasons.append("conditional_probability_below_threshold")
            if len(sources) < policy.minimum_source_diversity:
                reasons.append("source_diversity_below_threshold")
            if len(enterprises) < policy.minimum_enterprise_diversity:
                reasons.append("enterprise_diversity_below_threshold")
            if maximum_share > policy.maximum_enterprise_share:
                reasons.append("enterprise_concentration_above_threshold")
            if lower <= 0:
                reasons.append("bootstrap_interval_crosses_zero")
            if len(stable_slices) < policy.minimum_stable_slices:
                reasons.append("temporal_stability_below_threshold")
            if reasons:
                rejected.append(
                    RejectedDependencyCandidate(prerequisite, advanced, tuple(reasons))
                )
                continue
            accepted.append(
                DependencyCandidate(
                    prerequisite_skill_id=prerequisite,
                    advanced_skill_id=advanced,
                    dependency_score=round(score, 6),
                    probability_prerequisite_given_advanced=round(p_given, 6),
                    probability_advanced_given_prerequisite=round(reverse_p, 6),
                    joint_support=joint,
                    source_diversity=len(sources),
                    enterprise_diversity=len(enterprises),
                    maximum_enterprise_share=round(maximum_share, 6),
                    bootstrap_lower=round(lower, 6),
                    bootstrap_upper=round(upper, 6),
                    stable_slices=stable_slices,
                    evidence_ids=tuple(
                        sorted({evidence for item in supporting for evidence in item.evidence_ids})
                    ),
                )
            )
    return DependencyAnalysis(
        included_contexts=included,
        excluded_contexts=excluded,
        candidates=tuple(accepted),
        rejected=tuple(rejected),
    )
