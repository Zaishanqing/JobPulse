from __future__ import annotations

from dataclasses import dataclass


INTERNAL_CANDIDATE_ROLES = frozenset({"admin", "developer"})
CANDIDATE_DECISIONS = frozenset({"fit", "unfit"})


class CandidateRuleViolation(ValueError):
    """Raised when a candidate workflow invariant is violated."""


@dataclass(frozen=True)
class WeightedSkill:
    skill_id: str
    weight: float
    importance_level: str


@dataclass(frozen=True)
class CandidateMatch:
    score: float
    required_coverage: float
    matched: tuple[WeightedSkill, ...]
    missing: tuple[WeightedSkill, ...]
    bonus: tuple[WeightedSkill, ...]


def calculate_candidate_match(
    weights: tuple[WeightedSkill, ...], resume_skill_ids: frozenset[str]
) -> CandidateMatch:
    if not weights:
        raise CandidateRuleViolation("Enterprise job skill weights are empty")
    matched = tuple(item for item in weights if item.skill_id in resume_skill_ids)
    missing = tuple(
        item
        for item in weights
        if item.skill_id not in resume_skill_ids and item.importance_level != "bonus"
    )
    bonus = tuple(
        item
        for item in weights
        if item.skill_id in resume_skill_ids and item.importance_level == "bonus"
    )
    total_weight = sum(item.weight for item in weights) or 1.0
    return CandidateMatch(
        score=round(sum(item.weight for item in matched) / total_weight, 4),
        required_coverage=round(1 - len(missing) / max(len(weights), 1), 4),
        matched=matched,
        missing=missing,
        bonus=bonus,
    )


def require_submission_actor(role: str) -> None:
    if role != "personal_user":
        raise CandidateRuleViolation("Only a personal user can submit a resume")


def require_decision(decision: str) -> None:
    if decision not in CANDIDATE_DECISIONS:
        raise CandidateRuleViolation("Invalid candidate decision")
