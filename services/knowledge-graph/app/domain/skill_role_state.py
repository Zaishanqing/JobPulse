"""TEMP-02: Skill role state machine with hysteresis.

Classifies a skill's role within a position at a given release using
7 role states + 4 data states. Hysteresis thresholds dampen boundary
jitter. Every transition binds Release/GraphVersion/Evidence.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from app.domain.evolution import (
    _bounded,
    _evidence_quality,
    _weight,
)

SkillRoleState = Literal[
    "not_observed",
    "emerging",
    "bonus",
    "required",
    "core",
    "declining",
    "retired",
]

DataState = Literal[
    "unresolved",
    "insufficient_evidence",
    "source_concentrated",
    "blocked",
]

TransitionType = Literal[
    "entry",
    "promotion",
    "consolidation",
    "demotion",
    "exit",
    "reactivation",
    "no_change",
]

STATE_ORDER: tuple[SkillRoleState, ...] = (
    "not_observed",
    "emerging",
    "bonus",
    "required",
    "core",
)

DEFAULT_ROLE_STATE_CONFIG: dict[str, object] = {
    "policy_version": "skill-role-state-v1",
    "hysteresis": {
        "upper_not_observed_to_emerging": 0.15,
        "lower_emerging_to_not_observed": 0.08,
        "upper_emerging_to_bonus": 0.20,
        "lower_bonus_to_emerging": 0.12,
        "upper_bonus_to_required": 0.40,
        "lower_required_to_bonus": 0.28,
        "upper_required_to_core": 0.70,
        "lower_core_to_required": 0.55,
        "declining_delta_threshold": -0.15,
        "declining_recovery_threshold": -0.08,
        "retired_absence_windows": 2,
        "jitter_weight_delta_threshold": 0.04,
    },
    "coverage": {
        "min_independent_sources": 2,
        "min_independent_enterprises": 2,
        "min_independent_samples": 3,
    },
    "confidence": {
        "modality_signal": 0.35,
        "coverage_consistency": 0.25,
        "temporal_continuity": 0.25,
        "evidence_quality": 0.15,
    },
}


@dataclass(frozen=True)
class ModalityDistribution:
    required_share: float
    bonus_share: float
    total_coverage: float
    independent_jd_count: int
    independent_source_count: int
    independent_enterprise_count: int


@dataclass(frozen=True)
class CoverageStatus:
    coverage_ratio: float
    source_diversity: int
    data_state: DataState
    data_state_reason: str


@dataclass(frozen=True)
class EvidenceSpan:
    release_id: str
    graph_version_id: int
    observation_window_start: str
    observation_window_end: str
    sample_count: int
    catalog_snapshot_id: str
    watermark_config_version: str


@dataclass(frozen=True)
class SkillRoleObservation:
    position_id: str
    skill_id: str
    release_id: str
    role_state: SkillRoleState
    modality_distribution: ModalityDistribution
    evidence_span: EvidenceSpan
    coverage_status: CoverageStatus
    weight: float
    confidence: float
    policy_version: str
    observed_at: str


@dataclass(frozen=True)
class SkillRoleTransition:
    position_id: str
    skill_id: str
    before_state: SkillRoleState | None
    after_state: SkillRoleState | None
    before_observation: SkillRoleObservation | None
    after_observation: SkillRoleObservation | None
    transition_type: TransitionType
    transition_reasons: tuple[str, ...]
    evidence_delta: dict[str, object]
    policy_version: str
    occurred_at: str


def _hysteresis_threshold(
    going_up: bool,
    upper_key: str,
    lower_key: str,
    config: Mapping[str, object],
) -> float:
    hysteresis = dict(config["hysteresis"])
    return float(hysteresis[upper_key] if going_up else hysteresis[lower_key])


def compute_modality_distribution(
    skill_relations: Sequence[Mapping[str, object]],
    total_jds: int,
    source_ids: set[str],
    enterprise_ids: set[str],
) -> ModalityDistribution:
    total = max(total_jds, 1)
    required = 0
    bonus = 0
    for rel in skill_relations:
        modality = str(rel.get("modality", "")).lower()
        if modality == "required":
            required += 1
        elif modality in ("bonus", "preferred", "nice_to_have"):
            bonus += 1
        else:
            weight_val = _weight(rel)
            if weight_val >= 0.5:
                required += 1
            elif weight_val > 0:
                bonus += 1
    return ModalityDistribution(
        required_share=_bounded(required / total),
        bonus_share=_bounded(bonus / total),
        total_coverage=_bounded((required + bonus) / total),
        independent_jd_count=len(skill_relations),
        independent_source_count=len(source_ids),
        independent_enterprise_count=len(enterprise_ids),
    )


def assess_coverage_status(
    distribution: ModalityDistribution,
    source_ids: set[str],
    enterprise_ids: set[str],
    minimums: Mapping[str, int] | None = None,
) -> CoverageStatus:
    mins = minimums or DEFAULT_ROLE_STATE_CONFIG["coverage"]
    min_sources = int(mins["min_independent_sources"])
    min_enterprises = int(mins["min_independent_enterprises"])
    min_samples = int(mins["min_independent_samples"])

    reasons: list[str] = []
    if distribution.independent_jd_count < min_samples:
        reasons.append(f"samples: {distribution.independent_jd_count} < {min_samples}")
    if distribution.independent_source_count < min_sources:
        reasons.append(f"sources: {distribution.independent_source_count} < {min_sources}")
    if distribution.independent_enterprise_count < min_enterprises:
        reasons.append(f"enterprises: {distribution.independent_enterprise_count} < {min_enterprises}")

    if distribution.independent_jd_count < min_samples:
        return CoverageStatus(
            coverage_ratio=_bounded(distribution.independent_enterprise_count / max(min_enterprises, 1)),
            source_diversity=distribution.independent_source_count,
            data_state="insufficient_evidence",
            data_state_reason="; ".join(reasons),
        )
    # have enough samples, but sources or enterprises are concentrated
    if distribution.independent_source_count <= 1 or distribution.independent_enterprise_count <= 1:
        return CoverageStatus(
            coverage_ratio=_bounded(distribution.independent_enterprise_count / max(min_enterprises, 1)),
            source_diversity=distribution.independent_source_count,
            data_state="source_concentrated",
            data_state_reason="; ".join(reasons) if reasons else "signal concentrated in too few sources or enterprises",
        )
    return CoverageStatus(
        coverage_ratio=_bounded(distribution.independent_enterprise_count / max(min_enterprises, 1)),
        source_diversity=distribution.independent_source_count,
        data_state="unresolved",
        data_state_reason="",
    )


def classify_role_state(
    distribution: ModalityDistribution,
    coverage: CoverageStatus,
    previous_state: SkillRoleState | None,
    *,
    absent_window_count: int = 0,
    previous_weight: float | None = None,
    config: Mapping[str, object] | None = None,
) -> SkillRoleState:
    effective = dict(DEFAULT_ROLE_STATE_CONFIG, **(config or {}))
    hysteresis = dict(effective["hysteresis"])
    share = distribution.required_share

    if coverage.data_state in ("insufficient_evidence", "blocked", "source_concentrated"):
        if distribution.total_coverage == 0.0:
            return "not_observed"
        # Decouple evidence gate from role state: when evidence is temporarily
        # low but the skill was previously in a meaningful state, retain the
        # prior state instead of forcing not_observed. This prevents the
        # "evidence gate bounce" that is the primary jitter driver.
        if previous_state is not None and previous_state not in ("not_observed", "retired"):
            return previous_state
        return "not_observed"

    if distribution.total_coverage == 0.0:
        retired_absence = int(hysteresis["retired_absence_windows"])
        if absent_window_count >= retired_absence:
            return "retired"
        return "not_observed"

    # Jitter gate: weight deltas below this threshold are treated as noise.
    # Retains the previous state to prevent boundary flickering when the
    # underlying signal barely moves. Applies to "not_observed" prior states
    # as well (where the skill existed but was gated to not_observed by
    # source_concentrated/insufficient coverage): when coverage recovers with
    # a barely-changed weight, the skill has not genuinely emerged — retain
    # not_observed to avoid the "coverage bounce" jitter.
    jitter_threshold = float(hysteresis.get("jitter_weight_delta_threshold", 0.04))
    if (
        previous_state is not None
        and previous_weight is not None
        and previous_state not in ("retired",)
    ):
        delta = abs(share - previous_weight)
        if delta < jitter_threshold:
            return previous_state

    # check for declining
    delta_threshold = float(hysteresis["declining_delta_threshold"])
    recovery_threshold = float(hysteresis["declining_recovery_threshold"])
    if previous_state == "declining":
        if previous_weight is not None:
            delta = distribution.required_share - previous_weight
            if delta < recovery_threshold:
                return "declining"
        # recover from declining
    elif previous_weight is not None:
        delta = distribution.required_share - previous_weight
        if delta <= delta_threshold and previous_weight >= 0.10:
            return "declining"

    prev_is_declining = previous_state == "declining"
    prev_is_retired = previous_state == "retired"

    def _going_up() -> bool:
        if previous_state is None or prev_is_retired or prev_is_declining:
            return True
        try:
            return STATE_ORDER.index(previous_state) < STATE_ORDER.index("core")
        except ValueError:
            return True

    going_up = _going_up()

    upper_n2e = float(hysteresis["upper_not_observed_to_emerging"])
    lower_e2n = float(hysteresis["lower_emerging_to_not_observed"])
    upper_e2b = float(hysteresis["upper_emerging_to_bonus"])
    lower_b2e = float(hysteresis["lower_bonus_to_emerging"])
    upper_b2r = float(hysteresis["upper_bonus_to_required"])
    lower_r2b = float(hysteresis["lower_required_to_bonus"])
    upper_r2c = float(hysteresis["upper_required_to_core"])
    lower_c2r = float(hysteresis["lower_core_to_required"])

    # reactivation from retired starts at emerging if above threshold
    if prev_is_retired:
        if share >= upper_e2b:
            return "bonus"
        if share >= upper_n2e:
            return "emerging"
        return "retired"

    if going_up:
        if share >= upper_r2c:
            return "core"
        if share >= upper_b2r:
            return "required"
        if share >= upper_e2b:
            return "bonus"
        if share >= upper_n2e:
            return "emerging"
        if previous_state in ("emerging", "bonus", "required", "core", "declining"):
            if share >= lower_e2n:
                return previous_state
        return "not_observed"
    else:
        if share < lower_e2n:
            return "not_observed"
        if share < lower_b2e:
            if previous_state == "emerging":
                return "emerging"
            return "bonus" if share >= upper_e2b else "emerging"
        if share < lower_r2b:
            if previous_state == "bonus":
                return "bonus"
            return "required" if share >= upper_b2r else "bonus"
        if share < lower_c2r:
            if previous_state == "required":
                return "required"
            return "core" if share >= upper_r2c else "required"
        return previous_state or "core"


def produce_role_observation(
    position_id: str,
    skill_id: str,
    release_id: str,
    graph_version_id: int,
    window_start: str,
    window_end: str,
    catalog_snapshot_id: str,
    watermark_config_version: str,
    skill_relations: Sequence[Mapping[str, object]],
    total_jd_count: int,
    source_ids: set[str],
    enterprise_ids: set[str],
    *,
    previous_observation: SkillRoleObservation | None = None,
    absent_window_count: int = 0,
    observed_at: str | None = None,
    config: Mapping[str, object] | None = None,
) -> SkillRoleObservation:
    effective = dict(DEFAULT_ROLE_STATE_CONFIG, **(config or {}))
    distribution = compute_modality_distribution(
        skill_relations, total_jd_count, source_ids, enterprise_ids,
    )
    coverage = assess_coverage_status(distribution, source_ids, enterprise_ids)
    previous_state = previous_observation.role_state if previous_observation else None
    previous_weight = (
        previous_observation.modality_distribution.required_share
        if previous_observation else None
    )
    role_state = classify_role_state(
        distribution,
        coverage,
        previous_state,
        absent_window_count=absent_window_count,
        previous_weight=previous_weight,
        config=effective,
    )
    # confidence: weighted from modality signal, coverage consistency,
    # temporal continuity, and evidence quality
    conf_weights = dict(effective["confidence"])
    evidence_q = sum(
        _evidence_quality(rel) for rel in skill_relations
    ) / max(len(skill_relations), 1) if skill_relations else 0.25

    modality_confidence = _bounded(distribution.required_share + distribution.bonus_share)
    coverage_confidence = _bounded(distribution.independent_source_count / 6.0)
    temporal_confidence = 0.8 if previous_state is not None and role_state == previous_state else 0.5
    confidence = _bounded(
        float(conf_weights["modality_signal"]) * modality_confidence
        + float(conf_weights["coverage_consistency"]) * coverage_confidence
        + float(conf_weights["temporal_continuity"]) * temporal_confidence
        + float(conf_weights["evidence_quality"]) * evidence_q
    )
    return SkillRoleObservation(
        position_id=position_id,
        skill_id=skill_id,
        release_id=release_id,
        role_state=role_state,
        modality_distribution=distribution,
        evidence_span=EvidenceSpan(
            release_id=release_id,
            graph_version_id=graph_version_id,
            observation_window_start=window_start,
            observation_window_end=window_end,
            sample_count=len(skill_relations),
            catalog_snapshot_id=catalog_snapshot_id,
            watermark_config_version=watermark_config_version,
        ),
        coverage_status=coverage,
        weight=float(distribution.required_share),
        confidence=confidence,
        policy_version=str(effective.get("policy_version", "skill-role-state-v1")),
        observed_at=observed_at or "",
    )


def _classify_transition(
    before_state: SkillRoleState | None,
    after_state: SkillRoleState | None,
) -> TransitionType:
    if before_state is None and after_state is not None:
        return "entry"
    if before_state is not None and after_state is None:
        return "exit"
    if before_state == after_state:
        return "no_change"
    promotions = {
        ("not_observed", "emerging"): "entry",
        ("not_observed", "bonus"): "entry",
        ("not_observed", "required"): "entry",
        ("emerging", "bonus"): "promotion",
        ("emerging", "required"): "promotion",
        ("bonus", "required"): "promotion",
        ("required", "core"): "consolidation",
    }
    demotions = {
        ("core", "required"): "demotion",
        ("required", "bonus"): "demotion",
        ("bonus", "emerging"): "demotion",
        ("emerging", "not_observed"): "exit",
        ("required", "declining"): "demotion",
        ("core", "declining"): "demotion",
        ("bonus", "declining"): "demotion",
        ("emerging", "declining"): "demotion",
        ("declining", "not_observed"): "exit",
        ("declining", "retired"): "exit",
        ("not_observed", "retired"): "exit",
    }
    reactivations = {
        ("declining", "emerging"): "reactivation",
        ("declining", "bonus"): "reactivation",
        ("declining", "required"): "reactivation",
        ("retired", "emerging"): "reactivation",
        ("retired", "bonus"): "reactivation",
        ("retired", "not_observed"): "exit",
    }
    key = (before_state, after_state)  # type: ignore[assignment]
    if key in promotions:
        return promotions[key]
    if key in demotions:
        return demotions[key]
    if key in reactivations:
        return reactivations[key]
    return "no_change"


def detect_role_transition(
    before: SkillRoleObservation | None,
    after: SkillRoleObservation | None,
    *,
    occurred_at: str | None = None,
    config: Mapping[str, object] | None = None,
) -> SkillRoleTransition:
    effective = dict(DEFAULT_ROLE_STATE_CONFIG, **(config or {}))
    before_state = before.role_state if before else None
    after_state = after.role_state if after else None
    transition_type = _classify_transition(before_state, after_state)

    reasons: list[str] = []
    if transition_type == "entry":
        reasons.append("skill first observed in this position release")
    elif transition_type == "exit":
        reasons.append("skill no longer observed in this position release")
    elif transition_type == "promotion":
        reasons.append("increased required_share and coverage")
    elif transition_type == "demotion":
        reasons.append("decreased required_share or coverage")
    elif transition_type == "consolidation":
        reasons.append("skill firmly established as core")
    elif transition_type == "reactivation":
        reasons.append("previously declining or retired skill regained presence")

    evidence_delta: dict[str, object] = {}
    if before and after:
        evidence_delta = {
            "required_share_delta": round(
                after.modality_distribution.required_share
                - before.modality_distribution.required_share, 4
            ),
            "bonus_share_delta": round(
                after.modality_distribution.bonus_share
                - before.modality_distribution.bonus_share, 4
            ),
            "sample_count_delta": (
                after.modality_distribution.independent_jd_count
                - before.modality_distribution.independent_jd_count
            ),
            "source_count_delta": (
                after.modality_distribution.independent_source_count
                - before.modality_distribution.independent_source_count
            ),
        }
    return SkillRoleTransition(
        position_id=(after or before).position_id,  # type: ignore[union-attr]
        skill_id=(after or before).skill_id,  # type: ignore[union-attr]
        before_state=before_state,
        after_state=after_state,
        before_observation=before,
        after_observation=after,
        transition_type=transition_type,
        transition_reasons=tuple(reasons),
        evidence_delta=evidence_delta,
        policy_version=str(effective.get("policy_version", "skill-role-state-v1")),
        occurred_at=occurred_at or "",
    )
