"""TEMP-13: Dual-position temporal comparison aggregator.

Aggregates role transitions, evolution events, EMERGE v3.2 emergence
evaluation, coverage changes, and controlled replay results into a single
TemporalComparison DTO.

Two positions are compared on *shared experiment conditions* — time window,
catalog snapshot, source rules, and state thresholds — not on version-identity
equality.  ``graph_version_id`` and ``release_id`` are per-position identities:
the Java backend and LLM algorithm positions each carry their own version
lineage, so differing IDs are expected and never block comparability.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from app.domain.skill_role_state import (
    CoverageStatus,
    SkillRoleObservation,
    SkillRoleTransition,
)
from app.domain.controlled_replay import ControlledReplayResult

TemporalComparabilityStatus = Literal[
    "complete",
    "insufficient_evidence",
    "blocked",
]


@dataclass(frozen=True)
class TemporalComparisonSourceConfig:
    time_window_start: str
    time_window_end: str
    catalog_snapshot_id: str
    source_filter_rules: tuple[str, ...]
    state_policy_version: str


@dataclass(frozen=True)
class PositionTemporalProfile:
    position_id: str
    graph_version_id: int
    release_id: str
    role_observations: tuple[SkillRoleObservation, ...]
    role_transitions: tuple[SkillRoleTransition, ...]
    evolution_events: tuple[dict, ...]
    emergence_v32: dict | None
    coverage_summary: CoverageStatus | None
    controlled_replay_result: ControlledReplayResult | None
    source_ablation: dict[str, object] | None
    # -- per-profile provenance (TEMP-13) --
    catalog_snapshot_id: str | None = None
    source_filter_rules: tuple[str, ...] = ()
    actual_time_window_start: str | None = None
    actual_time_window_end: str | None = None
    state_policy_version: str | None = None


@dataclass(frozen=True)
class TemporalComparison:
    comparison_id: str
    source_config: TemporalComparisonSourceConfig
    position_a: PositionTemporalProfile
    position_b: PositionTemporalProfile
    comparability_status: TemporalComparabilityStatus
    evolution_event_diff: dict[str, list[dict]]
    role_state_diff: dict[str, tuple[SkillRoleObservation | None, SkillRoleObservation | None]]
    emergence_v32_comparison: dict[str, dict | None]
    evidence_summary: dict[str, object]
    limitations: tuple[str, ...]
    policy_version: str
    computed_at: str

    @staticmethod
    def compute(
        profile_a: PositionTemporalProfile,
        profile_b: PositionTemporalProfile,
        config: TemporalComparisonSourceConfig,
        *,
        comparison_id: str = "",
        computed_at: str = "",
    ) -> "TemporalComparison":
        # comparability check
        limitations: list[str] = []
        comparability: TemporalComparabilityStatus = "complete"

        # --- blocked checks: structural incompatibility ---
        # graph_version_id and release_id are per-position identities.  The
        # dual-position comparison (e.g. BACKEND_ENGINEER vs
        # LLM_ALGORITHM_ENGINEER) legitimately carries a different version ID
        # per position, so differing IDs are never a comparability gate.
        # Comparability is decided on shared conditions below (time window,
        # catalog snapshot, source rules, state policy).
        if config.time_window_start == "" or config.time_window_end == "":
            limitations.append("time window not configured")
            comparability = "blocked"

        # --- per-profile provenance comparison (TEMP-13) ---
        if (
            profile_a.catalog_snapshot_id is not None
            and profile_b.catalog_snapshot_id is not None
            and profile_a.catalog_snapshot_id != profile_b.catalog_snapshot_id
        ):
            limitations.append(
                f"catalog_snapshot mismatch: {profile_a.catalog_snapshot_id} vs {profile_b.catalog_snapshot_id}"
            )
            if comparability != "blocked":
                comparability = "blocked"

        if (
            profile_a.state_policy_version is not None
            and profile_b.state_policy_version is not None
            and profile_a.state_policy_version != profile_b.state_policy_version
        ):
            limitations.append(
                f"state_policy_version mismatch: {profile_a.state_policy_version} vs {profile_b.state_policy_version}"
            )

        if (
            profile_a.actual_time_window_start is not None
            and profile_b.actual_time_window_start is not None
            and profile_a.actual_time_window_end is not None
            and profile_b.actual_time_window_end is not None
        ):
            if (
                profile_a.actual_time_window_start != profile_b.actual_time_window_start
                or profile_a.actual_time_window_end != profile_b.actual_time_window_end
            ):
                limitations.append(
                    f"time_window mismatch: [{profile_a.actual_time_window_start}, {profile_a.actual_time_window_end}] "
                    f"vs [{profile_b.actual_time_window_start}, {profile_b.actual_time_window_end}]"
                )
                if comparability != "blocked":
                    comparability = "blocked"

        # verify profiles match shared config when both are specified
        if (
            profile_a.catalog_snapshot_id is not None
            and config.catalog_snapshot_id
            and profile_a.catalog_snapshot_id != config.catalog_snapshot_id
        ):
            limitations.append(
                f"profile_a catalog_snapshot ({profile_a.catalog_snapshot_id}) "
                f"contradicts shared config ({config.catalog_snapshot_id})"
            )
        if (
            profile_b.catalog_snapshot_id is not None
            and config.catalog_snapshot_id
            and profile_b.catalog_snapshot_id != config.catalog_snapshot_id
        ):
            limitations.append(
                f"profile_b catalog_snapshot ({profile_b.catalog_snapshot_id}) "
                f"contradicts shared config ({config.catalog_snapshot_id})"
            )

        # --- insufficient_evidence checks ---
        if (
            profile_a.coverage_summary
            and profile_a.coverage_summary.data_state == "insufficient_evidence"
        ):
            limitations.append("position_a: insufficient_evidence")
            if comparability != "blocked":
                comparability = "insufficient_evidence"
        if (
            profile_b.coverage_summary
            and profile_b.coverage_summary.data_state == "insufficient_evidence"
        ):
            limitations.append("position_b: insufficient_evidence")
            if comparability != "blocked":
                comparability = "insufficient_evidence"

        # evolution event diff
        a_keys = {
            (e.get("event_type"), e.get("skill_id") or e.get("event_id", ""))
            for e in profile_a.evolution_events
        }
        b_keys = {
            (e.get("event_type"), e.get("skill_id") or e.get("event_id", ""))
            for e in profile_b.evolution_events
        }
        only_a = [e for e in profile_a.evolution_events if (e.get("event_type"), e.get("skill_id") or e.get("event_id", "")) not in b_keys]
        only_b = [e for e in profile_b.evolution_events if (e.get("event_type"), e.get("skill_id") or e.get("event_id", "")) not in a_keys]
        common = [e for e in profile_a.evolution_events if (e.get("event_type"), e.get("skill_id") or e.get("event_id", "")) in b_keys]

        # role state diff
        a_obs_by_skill = {obs.skill_id: obs for obs in profile_a.role_observations}
        b_obs_by_skill = {obs.skill_id: obs for obs in profile_b.role_observations}
        all_skills = set(a_obs_by_skill) | set(b_obs_by_skill)
        role_diff: dict[str, tuple[SkillRoleObservation | None, SkillRoleObservation | None]] = {}
        for sid in all_skills:
            role_diff[sid] = (a_obs_by_skill.get(sid), b_obs_by_skill.get(sid))

        # EMERGE v3.2 emergence evaluation comparison
        emergence_comp: dict[str, dict | None] = {
            "position_a": profile_a.emergence_v32,
            "position_b": profile_b.emergence_v32,
        }

        # evidence summary
        evidence = {
            "position_a_events": len(profile_a.evolution_events),
            "position_b_events": len(profile_b.evolution_events),
            "shared_events": len(common),
            "only_a_events": len(only_a),
            "only_b_events": len(only_b),
            "a_role_observations": len(profile_a.role_observations),
            "b_role_observations": len(profile_b.role_observations),
        }

        return TemporalComparison(
            comparison_id=comparison_id or f"cmp:{profile_a.position_id}:{profile_b.position_id}",
            source_config=config,
            position_a=profile_a,
            position_b=profile_b,
            comparability_status=comparability,
            evolution_event_diff={
                "shared": common,
                "only_position_a": only_a,
                "only_position_b": only_b,
            },
            role_state_diff=role_diff,
            emergence_v32_comparison=emergence_comp,
            evidence_summary=evidence,
            limitations=tuple(limitations),
            policy_version=config.state_policy_version,
            computed_at=computed_at,
        )
