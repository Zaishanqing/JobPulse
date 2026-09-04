"""Auditable emerging candidate lifecycle state machine."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from app.domain.values import FrozenDict, JsonObject


CANDIDATE_STATUSES = (
    "weak_signal",
    "incubating",
    "emerging_candidate",
    "stable_emerging_role",
    "official_position",
    "dead",
    "noise",
)
PROMOTION_ORDER = {
    "weak_signal": "incubating",
    "incubating": "emerging_candidate",
    "emerging_candidate": "stable_emerging_role",
}
TERMINAL_STATUSES = frozenset({"dead", "noise"})
SUPPRESSED_MISSING_PENDING_IDENTITY_REVIEW = "pending_identity_review"

DEFAULT_CANDIDATE_LIFECYCLE_CONFIG: dict[str, object] = {
    "candidate_lifecycle_version": "candidate-lifecycle-v1",
    "identity_stability_threshold": 0.60,
    "weak_to_incubating_min_windows": 2,
    "weak_to_incubating_min_support": 2,
    "weak_to_incubating_min_companies": 1,
    "weak_to_incubating_min_emergence": 0.35,
    "incubating_to_emerging_min_windows": 3,
    "incubating_to_emerging_min_support": 3,
    "incubating_to_emerging_min_companies": 2,
    "incubating_to_emerging_min_emergence": 0.50,
    "incubating_to_emerging_min_identity_stability": 2,
    "emerging_to_stable_min_windows": 4,
    "emerging_to_stable_min_support": 4,
    "emerging_to_stable_min_companies": 3,
    "emerging_to_stable_min_emergence": 0.60,
    "emerging_to_stable_min_identity_stability": 3,
    "noise_min_windows": 2,
    "noise_max_support": 2,
    "noise_max_companies": 1,
    "noise_max_emergence": 0.30,
    "dead_after_missing_windows": 2,
}

CANDIDATE_LIFECYCLE_V2_VERSION = "candidate-lifecycle-v2"
DEFAULT_CANDIDATE_LIFECYCLE_V2_CONFIG: dict[str, object] = {
    **DEFAULT_CANDIDATE_LIFECYCLE_CONFIG,
    "candidate_lifecycle_version": CANDIDATE_LIFECYCLE_V2_VERSION,
    "noise_min_eligible_windows": 3,
    "noise_ema_alpha": 0.5,
    "noise_growth_tolerance": 0.0,
    "coverage_min_source_count": 1,
    "coverage_min_company_count": 1,
    "coverage_min_jd_count": 1,
    "lifecycle_v2_persistence_noise": True,
    "lifecycle_v2_coverage_missing": True,
}


@dataclass(frozen=True)
class LifecycleTransitionResult:
    to_status: str
    reason: str
    changed: bool
    triggered_rules: tuple[str, ...] = ()
    details: JsonObject = field(default_factory=FrozenDict)


@dataclass(frozen=True)
class LifecycleCoverageState:
    window_id: str
    valid: bool
    source_count: int = 0
    company_count: int = 0
    eligible_jd_count: int = 0
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class LifecycleTrajectorySignals:
    support_counts: tuple[int, ...] = ()
    company_counts: tuple[int, ...] = ()
    emergence_scores: tuple[float, ...] = ()
    eligible_window_count: int = 0
    coverage_state: LifecycleCoverageState | None = None


@dataclass(frozen=True)
class PromotionCondition:
    name: str
    required: int | float
    current: int | float
    missing: int | float
    satisfied: bool


@dataclass(frozen=True)
class StableGateAssessment:
    current_state: str
    eligible_state: bool
    gate_satisfied: bool
    conditions: tuple[PromotionCondition, ...]
    missing_conditions: tuple[str, ...]
    lifecycle_version: str
    config: Mapping[str, object]


def _merge_config(config: Mapping[str, object] | None) -> dict[str, object]:
    return {**DEFAULT_CANDIDATE_LIFECYCLE_CONFIG, **(config or {})}


def _merge_v2_config(config: Mapping[str, object] | None) -> dict[str, object]:
    return {**DEFAULT_CANDIDATE_LIFECYCLE_V2_CONFIG, **(config or {})}


def _policy_is_v2(config: Mapping[str, object] | None) -> bool:
    merged = _merge_config(config)
    return str(merged.get("candidate_lifecycle_version")) == CANDIDATE_LIFECYCLE_V2_VERSION


def empty_lifecycle_state_v2() -> JsonObject:
    return FrozenDict(
        {
            "schema_version": "candidate-lifecycle-trajectory.v2",
            "eligible_window_count": 0,
            "support_trajectory": (),
            "company_trajectory": (),
            "emergence_trajectory": (),
            "delta_support": 0,
            "delta_company": 0,
            "delta_emergence": 0.0,
            "ema_emergence": None,
            "missed_eligible_windows": 0,
            "missing_events": (),
        }
    )


def coverage_state_to_json(state: LifecycleCoverageState | None) -> JsonObject:
    if state is None:
        return FrozenDict()
    return FrozenDict(
        {
            "window_id": state.window_id,
            "valid": state.valid,
            "source_count": state.source_count,
            "company_count": state.company_count,
            "eligible_jd_count": state.eligible_jd_count,
            "reasons": state.reasons,
        }
    )


def trajectory_state_from_json(value: JsonObject | Mapping[str, object] | None) -> JsonObject:
    if value is None:
        return empty_lifecycle_state_v2()
    return FrozenDict(
        {
            "schema_version": str(value.get("schema_version", "candidate-lifecycle-trajectory.v2")),
            "eligible_window_count": int(value.get("eligible_window_count", 0)),
            "support_trajectory": tuple(int(item) for item in value.get("support_trajectory", ())),
            "company_trajectory": tuple(int(item) for item in value.get("company_trajectory", ())),
            "emergence_trajectory": tuple(
                float(item) for item in value.get("emergence_trajectory", ())
            ),
            "delta_support": int(value.get("delta_support", 0)),
            "delta_company": int(value.get("delta_company", 0)),
            "delta_emergence": float(value.get("delta_emergence", 0.0)),
            "ema_emergence": (
                float(value["ema_emergence"]) if value.get("ema_emergence") is not None else None
            ),
            "missed_eligible_windows": int(value.get("missed_eligible_windows", 0)),
            "missing_events": tuple(FrozenDict(item) for item in value.get("missing_events", ())),
        }
    )


def append_lifecycle_observation(
    state: JsonObject | Mapping[str, object] | None,
    *,
    support_count: int,
    company_count: int,
    emergence_score: float,
    eligible: bool,
    coverage_state: LifecycleCoverageState | None = None,
    config: Mapping[str, object] | None = None,
) -> JsonObject:
    merged = _merge_v2_config(config)
    previous = trajectory_state_from_json(state)
    support = tuple(previous["support_trajectory"])
    company = tuple(previous["company_trajectory"])
    emergence = tuple(previous["emergence_trajectory"])
    eligible_count = int(previous["eligible_window_count"])
    if eligible:
        support = (*support, int(support_count))
        company = (*company, int(company_count))
        emergence = (*emergence, float(emergence_score))
        eligible_count += 1
    delta_support = support[-1] - support[0] if len(support) >= 2 else 0
    delta_company = company[-1] - company[0] if len(company) >= 2 else 0
    delta_emergence = (
        round(emergence[-1] - emergence[0], 6) if len(emergence) >= 2 else 0.0
    )
    ema = None
    if emergence:
        alpha = float(merged.get("noise_ema_alpha", 0.5))
        ema = emergence[0]
        for value in emergence[1:]:
            ema = alpha * value + (1.0 - alpha) * ema
        ema = round(ema, 6)
    return FrozenDict(
        {
            "schema_version": "candidate-lifecycle-trajectory.v2",
            "eligible_window_count": eligible_count,
            "support_trajectory": support,
            "company_trajectory": company,
            "emergence_trajectory": emergence,
            "delta_support": delta_support,
            "delta_company": delta_company,
            "delta_emergence": delta_emergence,
            "ema_emergence": ema,
            "missed_eligible_windows": 0,
            "missing_events": previous["missing_events"],
            "last_coverage_state": coverage_state_to_json(coverage_state),
        }
    )


def record_missing_window(
    state: JsonObject | Mapping[str, object] | None,
    *,
    coverage_state: LifecycleCoverageState,
    config: Mapping[str, object] | None = None,
    suppression_reasons: tuple[str, ...] = (),
) -> JsonObject:
    merged = _merge_v2_config(config)
    previous = trajectory_state_from_json(state)
    suppressed = bool(suppression_reasons)
    if suppressed:
        missed = int(previous["missed_eligible_windows"])
        reason = "SUPPRESSED_PENDING_IDENTITY_REVIEW" if (
            SUPPRESSED_MISSING_PENDING_IDENTITY_REVIEW in suppression_reasons
        ) else "SUPPRESSED"
    else:
        if coverage_state.valid and bool(merged.get("lifecycle_v2_coverage_missing", True)):
            missed = int(previous["missed_eligible_windows"]) + 1
            reason = "NOT_OBSERVABLE"
        else:
            missed = int(previous["missed_eligible_windows"])
            reason = (
                "INSUFFICIENT_COVERAGE"
                if not coverage_state.valid
                else "NOT_OBSERVABLE"
            )
    missing_events = (
        *previous["missing_events"],
        FrozenDict(
            {
                "window_id": coverage_state.window_id,
                "reason": reason,
                "suppression_reasons": suppression_reasons,
                "coverage_state": coverage_state_to_json(coverage_state),
            }
        ),
    )
    return FrozenDict(
        {
            **previous,
            "missed_eligible_windows": missed,
            "missing_events": missing_events,
            "last_coverage_state": coverage_state_to_json(coverage_state),
        }
    )


def trajectory_signals_from_state(
    state: JsonObject | Mapping[str, object] | None,
    coverage_state: LifecycleCoverageState | None = None,
) -> LifecycleTrajectorySignals:
    value = trajectory_state_from_json(state)
    return LifecycleTrajectorySignals(
        support_counts=tuple(value["support_trajectory"]),
        company_counts=tuple(value["company_trajectory"]),
        emergence_scores=tuple(value["emergence_trajectory"]),
        eligible_window_count=int(value["eligible_window_count"]),
        coverage_state=coverage_state,
    )


def summarize_trajectory(
    signals: LifecycleTrajectorySignals,
    config: Mapping[str, object] | None = None,
) -> dict[str, object]:
    merged = _merge_v2_config(config)
    support = signals.support_counts
    company = signals.company_counts
    emergence = signals.emergence_scores
    delta_support = support[-1] - support[0] if len(support) >= 2 else 0
    delta_company = company[-1] - company[0] if len(company) >= 2 else 0
    delta_emergence = emergence[-1] - emergence[0] if len(emergence) >= 2 else 0.0
    ema = None
    if emergence:
        alpha = float(merged.get("noise_ema_alpha", 0.5))
        ema = emergence[0]
        for value in emergence[1:]:
            ema = alpha * value + (1.0 - alpha) * ema
        ema = round(ema, 6)
    growth_tolerance = float(merged.get("noise_growth_tolerance", 0.0))
    growth_observed = any(
        left < right
        for left, right in zip(support, support[1:], strict=False)
    ) or any(
        left < right
        for left, right in zip(company, company[1:], strict=False)
    ) or any(
        round(right - left, 6) > growth_tolerance
        for left, right in zip(emergence, emergence[1:], strict=False)
    )
    return {
        "eligible_window_count": signals.eligible_window_count,
        "support_trajectory": support,
        "company_trajectory": company,
        "emergence_trajectory": emergence,
        "delta_support": delta_support,
        "delta_company": delta_company,
        "delta_emergence": round(delta_emergence, 6),
        "ema_emergence": ema,
        "growth_observed": growth_observed,
    }


def promotion_requirements(
    target_status: str,
    config: Mapping[str, object] | None = None,
) -> tuple[tuple[str, int | float], ...]:
    """Return the production promotion thresholds for one lifecycle target."""
    merged = _merge_config(config)
    requirements: dict[str, tuple[tuple[str, int | float], ...]] = {
        "incubating": (
            ("windows", int(merged["weak_to_incubating_min_windows"])),
            ("support", int(merged["weak_to_incubating_min_support"])),
            ("companies", int(merged["weak_to_incubating_min_companies"])),
            ("emergence", float(merged["weak_to_incubating_min_emergence"])),
        ),
        "emerging_candidate": (
            ("windows", int(merged["incubating_to_emerging_min_windows"])),
            ("support", int(merged["incubating_to_emerging_min_support"])),
            ("companies", int(merged["incubating_to_emerging_min_companies"])),
            ("emergence", float(merged["incubating_to_emerging_min_emergence"])),
            (
                "identity_stability",
                int(merged["incubating_to_emerging_min_identity_stability"]),
            ),
        ),
        "stable_emerging_role": (
            ("windows", int(merged["emerging_to_stable_min_windows"])),
            ("support", int(merged["emerging_to_stable_min_support"])),
            ("companies", int(merged["emerging_to_stable_min_companies"])),
            ("emergence", float(merged["emerging_to_stable_min_emergence"])),
            (
                "identity_stability",
                int(merged["emerging_to_stable_min_identity_stability"]),
            ),
        ),
    }
    try:
        return requirements[target_status]
    except KeyError as exc:
        raise ValueError(f"unsupported promotion target: {target_status}") from exc


def assess_stable_gate(
    current_state: str,
    *,
    supported_window_count: int,
    support_count: int,
    company_count: int,
    emergence_score: float,
    identity_stability: int,
    config: Mapping[str, object] | None = None,
) -> StableGateAssessment:
    """Explain the production stable gate without performing a transition."""
    merged = _merge_config(config)
    actual = {
        "windows": supported_window_count,
        "support": support_count,
        "companies": company_count,
        "emergence": emergence_score,
        "identity_stability": identity_stability,
    }
    conditions = tuple(
        PromotionCondition(
            name=name,
            required=limit,
            current=actual[name],
            missing=max(0, limit - actual[name]),
            satisfied=actual[name] >= limit,
        )
        for name, limit in promotion_requirements("stable_emerging_role", merged)
    )
    already_stable = current_state == "stable_emerging_role"
    eligible_state = (
        PROMOTION_ORDER.get(current_state) == "stable_emerging_role" or already_stable
    )
    missing = [item.name for item in conditions if not item.satisfied]
    if not eligible_state:
        missing.insert(0, "current_state")
    return StableGateAssessment(
        current_state=current_state,
        eligible_state=eligible_state,
        gate_satisfied=already_stable or (eligible_state and not missing),
        conditions=conditions,
        missing_conditions=tuple(missing),
        lifecycle_version=str(merged["candidate_lifecycle_version"]),
        config=merged,
    )


def transition_candidate(
    current_status: str,
    *,
    supported_window_count: int,
    support_count: int,
    company_count: int,
    emergence_score: float,
    identity_similarity: float,
    identity_stability: int,
    config: Mapping[str, object] | None = None,
    trajectory: LifecycleTrajectorySignals | None = None,
) -> LifecycleTransitionResult:
    if _policy_is_v2(config):
        return _transition_candidate_v2(
            current_status,
            supported_window_count=supported_window_count,
            support_count=support_count,
            company_count=company_count,
            emergence_score=emergence_score,
            identity_similarity=identity_similarity,
            identity_stability=identity_stability,
            config=config,
            trajectory=trajectory,
        )
    merged = _merge_config(config)
    if current_status in TERMINAL_STATUSES or current_status == "official_position":
        return LifecycleTransitionResult(
            current_status,
            f"status {current_status} is terminal or already official",
            False,
            triggered_rules=(),
            details=FrozenDict(
                {
                    "previous_status": current_status,
                    "new_status": current_status,
                    "policy_version": str(merged["candidate_lifecycle_version"]),
                    "support_count": support_count,
                    "company_count": company_count,
                    "eligible_window_count": supported_window_count,
                    "emergence_score": emergence_score,
                    "triggered_rules": (),
                }
            ),
        )

    if (
        supported_window_count >= int(merged["noise_min_windows"])
        and support_count <= int(merged["noise_max_support"])
        and company_count <= int(merged["noise_max_companies"])
        and emergence_score <= float(merged["noise_max_emergence"])
    ):
        return LifecycleTransitionResult(
            "noise",
            "repeated low-quality evidence: support_count {}, company_count {}, "
            "emergence_score {} below noise thresholds".format(
                support_count, company_count, round(emergence_score, 6)
            ),
            True,
            triggered_rules=("legacy_noise",),
            details=FrozenDict(
                {
                    "previous_status": current_status,
                    "new_status": "noise",
                    "policy_version": str(merged["candidate_lifecycle_version"]),
                    "support_count": support_count,
                    "company_count": company_count,
                    "eligible_window_count": supported_window_count,
                    "emergence_score": emergence_score,
                    "triggered_rules": ("legacy_noise",),
                }
            ),
        )

    next_status = PROMOTION_ORDER.get(current_status)
    if next_status is None:
        return LifecycleTransitionResult(
            current_status,
            "no automatic promotion path; official_position requires explicit publication",
            False,
            triggered_rules=(),
            details=FrozenDict(
                {
                    "previous_status": current_status,
                    "new_status": current_status,
                    "policy_version": str(merged["candidate_lifecycle_version"]),
                    "support_count": support_count,
                    "company_count": company_count,
                    "eligible_window_count": supported_window_count,
                    "emergence_score": emergence_score,
                    "triggered_rules": (),
                }
            ),
        )

    requirements = promotion_requirements(next_status, merged)

    actual = {
        "windows": supported_window_count,
        "support": support_count,
        "companies": company_count,
        "emergence": emergence_score,
        "identity_stability": identity_stability,
    }
    missing = [
        name for name, limit in requirements if actual[name] < limit
    ]
    if missing:
        details = ", ".join(
            f"{name}={actual[name]}/{limit}" for name, limit in requirements
        )
        return LifecycleTransitionResult(
            current_status,
            f"{next_status} thresholds not met: {details}",
            False,
            triggered_rules=(),
            details=FrozenDict(
                {
                    "previous_status": current_status,
                    "new_status": current_status,
                    "policy_version": str(merged["candidate_lifecycle_version"]),
                    "support_count": support_count,
                    "company_count": company_count,
                    "eligible_window_count": supported_window_count,
                    "emergence_score": emergence_score,
                    "triggered_rules": (),
                }
            ),
        )
    return LifecycleTransitionResult(
        next_status,
        f"promoted from {current_status} to {next_status}; "
        f"windows={supported_window_count}, support={support_count}, "
        f"companies={company_count}, emergence={round(emergence_score, 6)}, "
        f"identity_stability={identity_stability}",
        True,
        triggered_rules=(f"promote_to_{next_status}",),
        details=FrozenDict(
            {
                "previous_status": current_status,
                "new_status": next_status,
                "policy_version": str(merged["candidate_lifecycle_version"]),
                "support_count": support_count,
                "company_count": company_count,
                "eligible_window_count": supported_window_count,
                "emergence_score": emergence_score,
                "identity_stability": identity_stability,
                "triggered_rules": (f"promote_to_{next_status}",),
            }
        ),
    )


def _transition_candidate_v2(
    current_status: str,
    *,
    supported_window_count: int,
    support_count: int,
    company_count: int,
    emergence_score: float,
    identity_similarity: float,
    identity_stability: int,
    config: Mapping[str, object] | None,
    trajectory: LifecycleTrajectorySignals | None,
) -> LifecycleTransitionResult:
    merged = _merge_v2_config(config)
    if current_status in TERMINAL_STATUSES or current_status == "official_position":
        return LifecycleTransitionResult(
            current_status,
            f"status {current_status} is terminal or already official",
            False,
            details=FrozenDict(
                {
                    "previous_status": current_status,
                    "new_status": current_status,
                    "policy_version": CANDIDATE_LIFECYCLE_V2_VERSION,
                    "support_count": support_count,
                    "company_count": company_count,
                    "eligible_window_count": (
                        trajectory.eligible_window_count if trajectory else supported_window_count
                    ),
                    "emergence_score": emergence_score,
                    "triggered_rules": (),
                }
            ),
        )

    signals = trajectory or LifecycleTrajectorySignals(
        support_counts=(support_count,),
        company_counts=(company_count,),
        emergence_scores=(emergence_score,),
        eligible_window_count=supported_window_count,
    )
    summary = summarize_trajectory(signals, merged)
    persistence_enabled = bool(merged.get("lifecycle_v2_persistence_noise", True))
    triggered: list[str] = []
    if persistence_enabled:
        if signals.eligible_window_count < int(merged["noise_min_eligible_windows"]):
            triggered.append("observing_min_eligible_windows")
        if any(
            item > int(merged["noise_max_support"]) for item in signals.support_counts
        ):
            triggered.append("support_not_low")
        if any(
            item > int(merged["noise_max_companies"]) for item in signals.company_counts
        ):
            triggered.append("company_not_low")
        if any(
            item > float(merged["noise_max_emergence"]) for item in signals.emergence_scores
        ):
            triggered.append("emergence_not_low")
        if bool(summary["growth_observed"]):
            triggered.append("growth_observed")
        noise = (
            signals.eligible_window_count >= int(merged["noise_min_eligible_windows"])
            and all(
                item <= int(merged["noise_max_support"]) for item in signals.support_counts
            )
            and all(
                item <= int(merged["noise_max_companies"])
                for item in signals.company_counts
            )
            and all(
                item <= float(merged["noise_max_emergence"])
                for item in signals.emergence_scores
            )
            and not bool(summary["growth_observed"])
        )
    else:
        noise = (
            supported_window_count >= int(merged["noise_min_windows"])
            and support_count <= int(merged["noise_max_support"])
            and company_count <= int(merged["noise_max_companies"])
            and emergence_score <= float(merged["noise_max_emergence"])
        )
        if noise:
            triggered.append("legacy_noise")
    if noise:
        rules = tuple(sorted(set(("persistent_low_quality", *triggered))))
        return LifecycleTransitionResult(
            "noise",
            "persistent low-quality evidence over {} eligible windows: support {}, "
            "companies {}, emergence {}; no growth".format(
                signals.eligible_window_count,
                support_count,
                company_count,
                round(emergence_score, 6),
            ),
            True,
            triggered_rules=rules,
            details=_v2_transition_details(
                current_status,
                "noise",
                support_count,
                company_count,
                emergence_score,
                identity_stability,
                signals,
                summary,
                rules,
            ),
        )

    next_status = PROMOTION_ORDER.get(current_status)
    if next_status is None:
        return LifecycleTransitionResult(
            current_status,
            "no automatic promotion path; official_position requires explicit publication",
            False,
            triggered_rules=tuple(triggered),
            details=_v2_transition_details(
                current_status,
                current_status,
                support_count,
                company_count,
                emergence_score,
                identity_stability,
                signals,
                summary,
                tuple(triggered),
            ),
        )

    requirements = promotion_requirements(next_status, merged)
    actual = {
        "windows": supported_window_count,
        "support": support_count,
        "companies": company_count,
        "emergence": emergence_score,
        "identity_stability": identity_stability,
    }
    missing = [name for name, limit in requirements if actual[name] < limit]
    if missing:
        details = ", ".join(
            f"{name}={actual[name]}/{limit}" for name, limit in requirements
        )
        return LifecycleTransitionResult(
            current_status,
            f"{next_status} thresholds not met: {details}",
            False,
            triggered_rules=tuple(triggered),
            details=_v2_transition_details(
                current_status,
                current_status,
                support_count,
                company_count,
                emergence_score,
                identity_stability,
                signals,
                summary,
                tuple(triggered),
            ),
        )
    rules = (*triggered, f"promote_to_{next_status}")
    return LifecycleTransitionResult(
        next_status,
        f"promoted from {current_status} to {next_status}; "
        f"windows={supported_window_count}, support={support_count}, "
        f"companies={company_count}, emergence={round(emergence_score, 6)}, "
        f"identity_stability={identity_stability}",
        True,
        triggered_rules=rules,
        details=_v2_transition_details(
            current_status,
            next_status,
            support_count,
            company_count,
            emergence_score,
            identity_stability,
            signals,
            summary,
            rules,
        ),
    )


def _v2_transition_details(
    previous_status: str,
    new_status: str,
    support_count: int,
    company_count: int,
    emergence_score: float,
    identity_stability: int,
    signals: LifecycleTrajectorySignals,
    summary: dict[str, object],
    triggered_rules: tuple[str, ...],
) -> JsonObject:
    return FrozenDict(
        {
            "previous_status": previous_status,
            "new_status": new_status,
            "policy_version": CANDIDATE_LIFECYCLE_V2_VERSION,
            "support_count": support_count,
            "company_count": company_count,
            "eligible_window_count": signals.eligible_window_count,
            "emergence_score": emergence_score,
            "ema_emergence": summary["ema_emergence"],
            "delta_support": summary["delta_support"],
            "delta_company": summary["delta_company"],
            "delta_emergence": summary["delta_emergence"],
            "identity_stability": identity_stability,
            "coverage_state": coverage_state_to_json(signals.coverage_state),
            "triggered_rules": triggered_rules,
        }
    )


def transition_for_missing_windows(
    current_status: str,
    missed_window_count: int,
    config: Mapping[str, object] | None = None,
    coverage_state: LifecycleCoverageState | None = None,
) -> LifecycleTransitionResult:
    if _policy_is_v2(config):
        merged = _merge_v2_config(config)
        if coverage_state is not None and not coverage_state.valid and bool(
            merged.get("lifecycle_v2_coverage_missing", True)
        ):
            return LifecycleTransitionResult(
                current_status,
                "INSUFFICIENT_COVERAGE: no disappearance evidence accumulated in "
                f"window {coverage_state.window_id}",
                False,
                triggered_rules=("coverage_insufficient",),
                details=FrozenDict(
                    {
                        "previous_status": current_status,
                        "new_status": current_status,
                        "policy_version": CANDIDATE_LIFECYCLE_V2_VERSION,
                        "coverage_state": coverage_state_to_json(coverage_state),
                        "missed_eligible_windows": missed_window_count,
                        "triggered_rules": ("coverage_insufficient",),
                    }
                ),
            )
        if current_status in TERMINAL_STATUSES or current_status == "official_position":
            return LifecycleTransitionResult(
                current_status,
                "status is terminal",
                False,
                details=FrozenDict(
                    {
                        "previous_status": current_status,
                        "new_status": current_status,
                        "policy_version": CANDIDATE_LIFECYCLE_V2_VERSION,
                        "coverage_state": coverage_state_to_json(coverage_state),
                        "missed_eligible_windows": missed_window_count,
                        "triggered_rules": (),
                    }
                ),
            )
        limit = int(merged["dead_after_missing_windows"])
        if missed_window_count >= limit:
            return LifecycleTransitionResult(
                "dead",
                f"no candidate support for {missed_window_count} eligible windows "
                f"(threshold {limit})",
                True,
                triggered_rules=("missing_eligible_windows",),
                details=FrozenDict(
                    {
                        "previous_status": current_status,
                        "new_status": "dead",
                        "policy_version": CANDIDATE_LIFECYCLE_V2_VERSION,
                        "coverage_state": coverage_state_to_json(coverage_state),
                        "missed_eligible_windows": missed_window_count,
                        "triggered_rules": ("missing_eligible_windows",),
                    }
                ),
            )
        return LifecycleTransitionResult(
            current_status,
            f"missed {missed_window_count} eligible windows, below dead threshold {limit}",
            False,
            details=FrozenDict(
                {
                    "previous_status": current_status,
                    "new_status": current_status,
                    "policy_version": CANDIDATE_LIFECYCLE_V2_VERSION,
                    "coverage_state": coverage_state_to_json(coverage_state),
                    "missed_eligible_windows": missed_window_count,
                    "triggered_rules": (),
                }
            ),
        )
    merged = _merge_config(config)
    if current_status in TERMINAL_STATUSES or current_status == "official_position":
        return LifecycleTransitionResult(
            current_status,
            "status is terminal",
            False,
            details=FrozenDict(
                {
                    "previous_status": current_status,
                    "new_status": current_status,
                    "policy_version": str(merged["candidate_lifecycle_version"]),
                    "missed_window_count": missed_window_count,
                    "triggered_rules": (),
                }
            ),
        )
    limit = int(merged["dead_after_missing_windows"])
    if missed_window_count >= limit:
        return LifecycleTransitionResult(
            "dead",
            f"no candidate support for {missed_window_count} consecutive windows "
            f"(threshold {limit})",
            True,
            triggered_rules=("legacy_missing",),
            details=FrozenDict(
                {
                    "previous_status": current_status,
                    "new_status": "dead",
                    "policy_version": str(merged["candidate_lifecycle_version"]),
                    "missed_window_count": missed_window_count,
                    "triggered_rules": ("legacy_missing",),
                }
            ),
        )
    return LifecycleTransitionResult(
        current_status,
        f"missed {missed_window_count} windows, below dead threshold {limit}",
        False,
        details=FrozenDict(
            {
                "previous_status": current_status,
                "new_status": current_status,
                "policy_version": str(merged["candidate_lifecycle_version"]),
                "missed_window_count": missed_window_count,
                "triggered_rules": (),
            }
        ),
    )
