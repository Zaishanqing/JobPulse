"""Identity v3 ranking challenger (challenger-only).

Identity v3 re-ranks an already frozen retrieval pool with a deterministic,
versioned score.  It never uses a single component OR-rule, keeps recent
continuity and stable anchor evidence as separate signals, and applies a
generic-title contradiction guard so a high title similarity cannot override
responsibility/skill contradiction.

The accept threshold, review margin, and top-2 margin are reused from the
frozen Identity v2 selected config.  No production linker is replaced.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping


IDENTITY_V3_DECISION_VERSION = "identity-v3-ranking.v1"
IDENTITY_V3_CONFIG_BASE_VERSION = "identity-v3-ranking.v1"

DEFAULT_IDENTITY_V3_CONFIG: dict[str, Any] = {
    "recent_continuity_weight": 0.24,
    "stable_responsibility_weight": 0.14,
    "discriminative_skills_weight": 0.18,
    "title_compatibility_weight": 0.26,
    "temporal_adjacency_weight": 0.08,
    "membership_continuity_weight": 0.05,
    "alias_support_weight": 0.05,
    "recent_title_sub_weight": 0.35,
    "recent_responsibility_sub_weight": 0.30,
    "recent_skill_sub_weight": 0.20,
    "recent_max_sub_weight": 0.15,
    "recent_strong_bonus_weight": 0.05,
    "recent_strong_threshold": 0.55,
    "contradiction_penalty_weight": 0.45,
    "contradiction_penalty_value": 1.0,
    "title_contradiction_threshold": 0.25,
    "responsibility_contradiction_threshold": 0.15,
    "skill_contradiction_threshold": 0.45,
    "verifier_accept_threshold": 0.26,
    "verifier_review_margin": 0.05,
    "verifier_top2_margin": 0.05,
}

IDENTITY_V3_DELTA_VERSION = "identity-v3-ranking-delta.v1"
IDENTITY_V3_DELTA_CONFIG_BASE_VERSION = "identity-v3-ranking-delta.v1"

DEFAULT_IDENTITY_V3_DELTA_CONFIG: dict[str, Any] = {
    "v2_base_score": True,
    "discriminative_skill_bonus_weight": 0.05,
    "discriminative_skill_threshold": 0.50,
    "discriminative_skill_min_title": 0.40,
    "contradiction_penalty_weight": 0.06,
    "contradiction_penalty_value": 1.0,
    "contradiction_title_threshold": 0.30,
    "contradiction_skill_threshold": 0.45,
    "contradiction_anchor_responsibility_threshold": 0.15,
    "recent_strong_bonus_weight": 0.02,
    "recent_strong_threshold": 0.65,
    "left_continuity_bonus_weight": 0.06,
    "verifier_accept_threshold": 0.26,
    "verifier_review_margin": 0.05,
    "verifier_top2_margin": 0.05,
}

IDENTITY_V3_DECISION_VERSION = "identity-v3-decision.v1"
IDENTITY_V3_DECISION_CONFIG_BASE_VERSION = "identity-v3-decision.v1"

DEFAULT_IDENTITY_V3_DECISION_CONFIG: dict[str, Any] = {
    "verifier_accept_threshold": 0.26,
    "verifier_review_margin": 0.05,
    "verifier_top2_margin": 0.05,
    "continuity_min_contribution": 0.0,
    "contradiction_max_contribution": 0.0,
    "evidence_confidence_min": 0.0,
}


@dataclass(frozen=True)
class IdentityV3RankComponents:
    recent_continuity: float
    stable_responsibility: float
    discriminative_skills: float
    title_compatibility: float
    temporal_adjacency: float
    membership_continuity: float
    alias_support: float
    recent_strong_bonus: float
    contradiction_penalty: float
    evidence_confidence: float
    semantic_status: str
    final_score: float
    decomposition: dict[str, Any]
    weights: tuple[float, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "recent_continuity": self.recent_continuity,
            "stable_responsibility": self.stable_responsibility,
            "discriminative_skills": self.discriminative_skills,
            "title_compatibility": self.title_compatibility,
            "temporal_adjacency": self.temporal_adjacency,
            "membership_continuity": self.membership_continuity,
            "alias_support": self.alias_support,
            "recent_strong_bonus": self.recent_strong_bonus,
            "contradiction_penalty": self.contradiction_penalty,
            "evidence_confidence": self.evidence_confidence,
            "semantic_status": self.semantic_status,
            "final_score": self.final_score,
            "decomposition": dict(self.decomposition),
            "weights": list(self.weights),
        }


@dataclass(frozen=True)
class IdentityV3DeltaRankComponents:
    v2_base_score: float
    skill_bonus: float
    skill_bonus_contribution: float
    contradiction_penalty: float
    contradiction_contribution: float
    recent_strong_bonus: float
    recent_strong_contribution: float
    left_continuity_bonus: float
    left_continuity_contribution: float
    evidence_confidence: float
    semantic_status: str
    final_score: float
    decomposition: dict[str, Any]
    weights: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "v2_base_score": self.v2_base_score,
            "skill_bonus": self.skill_bonus,
            "skill_bonus_contribution": self.skill_bonus_contribution,
            "contradiction_penalty": self.contradiction_penalty,
            "contradiction_contribution": self.contradiction_contribution,
            "recent_strong_bonus": self.recent_strong_bonus,
            "recent_strong_contribution": self.recent_strong_contribution,
            "left_continuity_bonus": self.left_continuity_bonus,
            "left_continuity_contribution": self.left_continuity_contribution,
            "evidence_confidence": self.evidence_confidence,
            "semantic_status": self.semantic_status,
            "final_score": self.final_score,
            "decomposition": dict(self.decomposition),
            "weights": dict(self.weights),
        }


@dataclass(frozen=True)
class IdentityV3Decision:
    """Deterministic challenger decision on an Identity v3 top-K ranking."""

    selected_candidate_id: str | None
    decision: str
    score: float | None
    top2_gap: float | None
    continuity_contribution: float
    contradiction_contribution: float
    decision_basis: tuple[str, ...]
    review_reason: str | None
    accept_threshold: float
    review_margin: float
    top2_margin: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_candidate_id": self.selected_candidate_id,
            "decision": self.decision,
            "score": self.score,
            "top2_gap": self.top2_gap,
            "continuity_contribution": self.continuity_contribution,
            "contradiction_contribution": self.contradiction_contribution,
            "decision_basis": list(self.decision_basis),
            "review_reason": self.review_reason,
            "accept_threshold": self.accept_threshold,
            "review_margin": self.review_margin,
            "top2_margin": self.top2_margin,
        }


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _identity_v3_config(
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    merged = {**DEFAULT_IDENTITY_V3_CONFIG, **(config or {})}
    positive_keys = (
        "recent_continuity_weight",
        "stable_responsibility_weight",
        "discriminative_skills_weight",
        "title_compatibility_weight",
        "temporal_adjacency_weight",
        "membership_continuity_weight",
        "alias_support_weight",
    )
    positive_weights = [float(merged[key]) for key in positive_keys]
    if any(value < 0 for value in positive_weights):
        raise ValueError("identity v3 positive weights must be non-negative")
    if abs(sum(positive_weights) - 1.0) > 1e-9:
        raise ValueError("identity v3 positive weights must sum to one")
    sub_keys = (
        "recent_title_sub_weight",
        "recent_responsibility_sub_weight",
        "recent_skill_sub_weight",
        "recent_max_sub_weight",
    )
    sub_weights = [float(merged[key]) for key in sub_keys]
    if any(value < 0 for value in sub_weights):
        raise ValueError("identity v3 recent sub weights must be non-negative")
    if abs(sum(sub_weights) - 1.0) > 1e-9:
        raise ValueError("identity v3 recent sub weights must sum to one")
    if float(merged["recent_strong_bonus_weight"]) < 0:
        raise ValueError("identity v3 recent strong bonus weight must be non-negative")
    if float(merged["contradiction_penalty_weight"]) < 0:
        raise ValueError(
            "identity v3 contradiction penalty weight must be non-negative"
        )
    for key in (
        "recent_strong_threshold",
        "title_contradiction_threshold",
        "responsibility_contradiction_threshold",
        "skill_contradiction_threshold",
        "verifier_accept_threshold",
        "verifier_review_margin",
        "verifier_top2_margin",
    ):
        if not 0 <= float(merged[key]) <= 1:
            raise ValueError(f"identity v3 {key} must be between zero and one")
    return merged


def identity_v3_config_version(
    config: Mapping[str, Any] | None = None,
) -> str:
    merged = _identity_v3_config(config)
    digest = sha256(_canonical_json(merged).encode("utf-8")).hexdigest()[:16]
    return f"{IDENTITY_V3_CONFIG_BASE_VERSION}/sha256:{digest}"


def _recent_continuity_score(
    components: Mapping[str, Any],
    config: Mapping[str, Any],
) -> float:
    title = float(components.get("title_recent") or 0.0)
    responsibility = float(components.get("responsibility_recent") or 0.0)
    skill = float(components.get("skill_recent") or 0.0)
    max_signal = max(title, responsibility, skill)
    return round(
        float(config["recent_title_sub_weight"]) * title
        + float(config["recent_responsibility_sub_weight"]) * responsibility
        + float(config["recent_skill_sub_weight"]) * skill
        + float(config["recent_max_sub_weight"]) * max_signal,
        6,
    )


def compute_identity_v3_rank(
    *,
    components: Mapping[str, Any],
    temporal_prior: float,
    config: Mapping[str, Any] | None = None,
) -> IdentityV3RankComponents:
    """Compute the deterministic Identity v3 rank score and decomposition."""
    merged = _identity_v3_config(config)
    evidence_confidence = float(components.get("evidence_confidence") or 0.0)
    title_recent = float(components.get("title_recent") or 0.0)
    title_anchor = float(components.get("title_anchor") or 0.0)
    responsibility_recent = float(components.get("responsibility_recent") or 0.0)
    responsibility_anchor = float(components.get("responsibility_anchor") or 0.0)
    skill_recent = float(components.get("skill_recent") or 0.0)
    skill_anchor = float(components.get("skill_anchor") or 0.0)
    alias_support = max(
        float(components.get("title_alias_support") or 0.0),
        float(components.get("responsibility_alias_support") or 0.0),
    )

    recent_continuity = _recent_continuity_score(components, merged)
    stable_responsibility = round(
        responsibility_anchor * evidence_confidence,
        6,
    )
    discriminative_skills = round(
        max(
            skill_recent,
            skill_anchor * evidence_confidence,
        ),
        6,
    )
    title_compatibility = round(
        max(title_recent, title_anchor * evidence_confidence),
        6,
    )
    membership = float(components.get("membership_overlap") or 0.0)
    semantic = components.get("semantic_similarity")
    semantic_status = "available" if semantic is not None else "unavailable"

    recent_strong = max(
        title_recent,
        responsibility_recent,
        skill_recent,
    )
    strong_bonus = (
        1.0
        if recent_strong >= float(merged["recent_strong_threshold"])
        else 0.0
    )
    title_high = max(title_recent, title_anchor) >= float(
        merged["title_contradiction_threshold"]
    )
    responsibility_anchor_low = responsibility_anchor <= float(
        merged["responsibility_contradiction_threshold"]
    )
    skill_low = max(skill_recent, skill_anchor) <= float(
        merged["skill_contradiction_threshold"]
    )
    contradiction = (
        float(merged["contradiction_penalty_value"])
        if title_high and responsibility_anchor_low and skill_low
        else 0.0
    )

    components_with_values = (
        ("recent_continuity", recent_continuity),
        ("stable_responsibility", stable_responsibility),
        ("discriminative_skills", discriminative_skills),
        ("title_compatibility", title_compatibility),
        ("temporal_adjacency", temporal_prior),
        ("membership_continuity", membership),
        ("alias_support", alias_support),
    )
    positive = sum(
        value * float(merged[f"{name}_weight"])
        for name, value in components_with_values
    )
    bonus_contribution = (
        float(merged["recent_strong_bonus_weight"]) * strong_bonus
    )
    penalty_contribution = (
        float(merged["contradiction_penalty_weight"]) * contradiction
    )
    final_score = round(
        min(
            1.0,
            max(0.0, positive + bonus_contribution - penalty_contribution),
        ),
        6,
    )
    decomposition = {
        "positive_components": {
            name: {
                "score": round(value, 6),
                "weight": round(float(merged[f"{name}_weight"]), 6),
                "contribution": round(
                    value * float(merged[f"{name}_weight"]),
                    6,
                ),
            }
            for name, value in components_with_values
        },
        "recent_strong_bonus": {
            "score": round(strong_bonus, 6),
            "weight": round(float(merged["recent_strong_bonus_weight"]), 6),
            "contribution": round(bonus_contribution, 6),
        },
        "contradiction_penalty": {
            "score": round(contradiction, 6),
            "weight": round(float(merged["contradiction_penalty_weight"]), 6),
            "contribution": round(penalty_contribution, 6),
        },
        "positive_sum": round(positive, 6),
        "final_score": final_score,
    }
    return IdentityV3RankComponents(
        recent_continuity=recent_continuity,
        stable_responsibility=stable_responsibility,
        discriminative_skills=discriminative_skills,
        title_compatibility=title_compatibility,
        temporal_adjacency=temporal_prior,
        membership_continuity=membership,
        alias_support=alias_support,
        recent_strong_bonus=strong_bonus,
        contradiction_penalty=contradiction,
        evidence_confidence=evidence_confidence,
        semantic_status=semantic_status,
        final_score=final_score,
        decomposition=decomposition,
        weights=tuple(
            float(merged[key])
            for key in (
                "recent_continuity_weight",
                "stable_responsibility_weight",
                "discriminative_skills_weight",
                "title_compatibility_weight",
                "temporal_adjacency_weight",
                "membership_continuity_weight",
                "alias_support_weight",
                "recent_strong_bonus_weight",
                "contradiction_penalty_weight",
            )
        ),
    )


def _identity_v3_delta_config(
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    merged = {**DEFAULT_IDENTITY_V3_DELTA_CONFIG, **(config or {})}
    for key in (
        "discriminative_skill_bonus_weight",
        "contradiction_penalty_weight",
        "recent_strong_bonus_weight",
        "left_continuity_bonus_weight",
        "contradiction_penalty_value",
    ):
        if float(merged[key]) < 0:
            raise ValueError(f"identity v3 delta {key} must be non-negative")
    for key in (
        "discriminative_skill_threshold",
        "discriminative_skill_min_title",
        "contradiction_title_threshold",
        "contradiction_skill_threshold",
        "contradiction_anchor_responsibility_threshold",
        "recent_strong_threshold",
        "verifier_accept_threshold",
        "verifier_review_margin",
        "verifier_top2_margin",
    ):
        if not 0 <= float(merged[key]) <= 1:
            raise ValueError(f"identity v3 delta {key} must be between zero and one")
    return merged


def identity_v3_delta_config_version(
    config: Mapping[str, Any] | None = None,
) -> str:
    merged = _identity_v3_delta_config(config)
    digest = sha256(_canonical_json(merged).encode("utf-8")).hexdigest()[:16]
    return f"{IDENTITY_V3_DELTA_CONFIG_BASE_VERSION}/sha256:{digest}"


def _identity_v3_decision_config(
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    merged = {**DEFAULT_IDENTITY_V3_DECISION_CONFIG, **(config or {})}
    for key in (
        "verifier_accept_threshold",
        "verifier_review_margin",
        "verifier_top2_margin",
        "continuity_min_contribution",
        "contradiction_max_contribution",
        "evidence_confidence_min",
    ):
        if not 0 <= float(merged[key]) <= 1:
            raise ValueError(f"identity v3 decision {key} must be between zero and one")
    return merged


def identity_v3_decision_config_version(
    config: Mapping[str, Any] | None = None,
) -> str:
    merged = _identity_v3_decision_config(config)
    digest = sha256(_canonical_json(merged).encode("utf-8")).hexdigest()[:16]
    return f"{IDENTITY_V3_DECISION_CONFIG_BASE_VERSION}/sha256:{digest}"


def decide_identity_v3(
    *,
    top1_score: float,
    top2_score: float | None,
    top1_continuity_contribution: float,
    top1_contradiction_contribution: float,
    top1_evidence_confidence: float,
    selected_candidate_id: str | None = None,
    config: Mapping[str, Any] | None = None,
) -> IdentityV3Decision:
    """Decide same/review/new from the Identity v3 top-1/top-2 decomposition.

    Automatic ``same`` requires sufficient top-1 evidence, no severe
    contradiction, reasonable continuity evidence, and an acceptable top-2 gap.
    Anything genuinely uncertain is sent to review; a far-below-threshold top-1
    is treated as a new identity.
    """
    merged = _identity_v3_decision_config(config)
    accept = float(merged["verifier_accept_threshold"])
    review_margin = float(merged["verifier_review_margin"])
    top2_margin = float(merged["verifier_top2_margin"])
    continuity_min = float(merged["continuity_min_contribution"])
    contradiction_max = float(merged["contradiction_max_contribution"])
    confidence_min = float(merged["evidence_confidence_min"])

    margin = round(top1_score - accept, 6)
    top2_gap = (
        round(top1_score - float(top2_score), 6)
        if top2_score is not None
        else None
    )
    basis: list[str] = []
    matched = top1_score >= accept
    if matched:
        if top1_contradiction_contribution > contradiction_max:
            basis.append("severe_contradiction")
        if top1_continuity_contribution < continuity_min:
            basis.append("insufficient_continuity")
        if top1_evidence_confidence < confidence_min:
            basis.append("low_evidence_confidence")
        if top2_gap is not None and top2_gap <= top2_margin:
            basis.append("top2_ambiguity")
        if basis:
            decision = "review_required"
        else:
            decision = "same"
    elif margin > -review_margin:
        basis.append("below_accept_by_review_margin")
        decision = "review_required"
    else:
        basis.append("score_below_accept")
        decision = "new"

    review_reason = "; ".join(basis) if basis and decision == "review_required" else None
    selected = selected_candidate_id if decision != "new" else None
    return IdentityV3Decision(
        selected_candidate_id=selected,
        decision=decision,
        score=round(top1_score, 6),
        top2_gap=top2_gap,
        continuity_contribution=round(top1_continuity_contribution, 6),
        contradiction_contribution=round(top1_contradiction_contribution, 6),
        decision_basis=tuple(basis),
        review_reason=review_reason,
        accept_threshold=accept,
        review_margin=review_margin,
        top2_margin=top2_margin,
    )


def compute_identity_v3_delta_rank(
    *,
    v2_base_score: float,
    components: Mapping[str, Any],
    temporal_prior: float,
    left_candidate_continuity: bool = False,
    config: Mapping[str, Any] | None = None,
) -> IdentityV3DeltaRankComponents:
    """Compute a v2-stable rank score plus small v3 corrections.

    The v2 verifier score is kept as the base so already-correct rankings are
    not destabilised.  The v3 delta only adds a discriminative-skill bonus,
    a generic-title contradiction penalty, and a strong-recent bonus.
    """
    merged = _identity_v3_delta_config(config)
    title_recent = float(components.get("title_recent") or 0.0)
    title_anchor = float(components.get("title_anchor") or 0.0)
    responsibility_recent = float(components.get("responsibility_recent") or 0.0)
    responsibility_anchor = float(components.get("responsibility_anchor") or 0.0)
    skill_recent = float(components.get("skill_recent") or 0.0)
    skill_anchor = float(components.get("skill_anchor") or 0.0)
    evidence_confidence = float(components.get("evidence_confidence") or 0.0)
    semantic = components.get("semantic_similarity")
    semantic_status = "available" if semantic is not None else "unavailable"

    skill_bonus = (
        1.0
        if (
            skill_recent >= float(merged["discriminative_skill_threshold"])
            and title_recent >= float(merged["discriminative_skill_min_title"])
        )
        else 0.0
    )
    title_high = max(title_recent, title_anchor) >= float(
        merged["contradiction_title_threshold"]
    )
    skill_low = max(skill_recent, skill_anchor) <= float(
        merged["contradiction_skill_threshold"]
    )
    anchor_responsibility_low = responsibility_anchor <= float(
        merged["contradiction_anchor_responsibility_threshold"]
    )
    contradiction = (
        float(merged["contradiction_penalty_value"])
        if title_high and skill_low and anchor_responsibility_low
        else 0.0
    )
    recent_strong = max(
        title_recent,
        responsibility_recent,
        skill_recent,
    )
    strong_bonus = (
        1.0
        if recent_strong >= float(merged["recent_strong_threshold"])
        else 0.0
    )
    skill_contribution = (
        float(merged["discriminative_skill_bonus_weight"]) * skill_bonus
    )
    contradiction_contribution = (
        float(merged["contradiction_penalty_weight"]) * contradiction
    )
    strong_contribution = (
        float(merged["recent_strong_bonus_weight"]) * strong_bonus
    )
    left_continuity_bonus = 1.0 if left_candidate_continuity else 0.0
    left_continuity_contribution = (
        float(merged["left_continuity_bonus_weight"]) * left_continuity_bonus
    )
    final_score = round(
        min(
            1.0,
            max(
                0.0,
                v2_base_score
                + skill_contribution
                - contradiction_contribution
                + strong_contribution
                + left_continuity_contribution,
            ),
        ),
        6,
    )
    decomposition = {
        "v2_base_score": round(v2_base_score, 6),
        "discriminative_skill_bonus": {
            "score": round(skill_bonus, 6),
            "weight": round(float(merged["discriminative_skill_bonus_weight"]), 6),
            "contribution": round(skill_contribution, 6),
        },
        "contradiction_penalty": {
            "score": round(contradiction, 6),
            "weight": round(float(merged["contradiction_penalty_weight"]), 6),
            "contribution": round(contradiction_contribution, 6),
        },
        "recent_strong_bonus": {
            "score": round(strong_bonus, 6),
            "weight": round(float(merged["recent_strong_bonus_weight"]), 6),
            "contribution": round(strong_contribution, 6),
        },
        "left_continuity_bonus": {
            "score": round(left_continuity_bonus, 6),
            "weight": round(float(merged["left_continuity_bonus_weight"]), 6),
            "contribution": round(left_continuity_contribution, 6),
        },
        "temporal_prior": round(temporal_prior, 6),
        "final_score": final_score,
    }
    return IdentityV3DeltaRankComponents(
        v2_base_score=v2_base_score,
        skill_bonus=skill_bonus,
        skill_bonus_contribution=round(skill_contribution, 6),
        contradiction_penalty=contradiction,
        contradiction_contribution=round(contradiction_contribution, 6),
        recent_strong_bonus=strong_bonus,
        recent_strong_contribution=round(strong_contribution, 6),
        left_continuity_bonus=left_continuity_bonus,
        left_continuity_contribution=round(left_continuity_contribution, 6),
        evidence_confidence=evidence_confidence,
        semantic_status=semantic_status,
        final_score=final_score,
        decomposition=decomposition,
        weights={
            "discriminative_skill_bonus_weight": float(
                merged["discriminative_skill_bonus_weight"]
            ),
            "contradiction_penalty_weight": float(
                merged["contradiction_penalty_weight"]
            ),
            "recent_strong_bonus_weight": float(
                merged["recent_strong_bonus_weight"]
            ),
            "left_continuity_bonus_weight": float(
                merged["left_continuity_bonus_weight"]
            ),
        },
    )
