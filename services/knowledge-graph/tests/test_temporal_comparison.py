"""Tests for TEMP-13: Dual-position temporal comparison."""

from __future__ import annotations

import pytest

from app.domain.skill_role_state import (
    SkillRoleObservation,
    SkillRoleTransition,
    assess_coverage_status,
    compute_modality_distribution,
    detect_role_transition,
)
from app.domain.temporal_comparison import (
    TemporalComparison,
    TemporalComparisonSourceConfig,
    PositionTemporalProfile,
)


def _config() -> TemporalComparisonSourceConfig:
    return TemporalComparisonSourceConfig(
        time_window_start="2026-01-01",
        time_window_end="2026-01-07",
        catalog_snapshot_id="CAT-v1",
        source_filter_rules=("all",),
        state_policy_version="v1",
    )


def _profile(
    position_id: str,
    release_id: str,
    graph_version_id: int = 1,
    coverage_override: object = None,
    *,
    catalog_snapshot_id: str | None = "CAT-v1",
    actual_time_window_start: str | None = "2026-01-01",
    actual_time_window_end: str | None = "2026-01-07",
    state_policy_version: str | None = "v1",
    source_filter_rules: tuple[str, ...] = ("all",),
) -> PositionTemporalProfile:
    relations = [
        {"modality": "required", "weight": 0.8},
        {"modality": "required", "weight": 0.7},
        {"modality": "bonus", "weight": 0.5},
    ]
    dist = compute_modality_distribution(relations, 5, {"s1", "s2"}, {"e1", "e2", "e3"})
    coverage = assess_coverage_status(dist, {"s1", "s2"}, {"e1", "e2"})
    obs = SkillRoleObservation(
        position_id=position_id,
        skill_id="SKILL_TEST",
        release_id=release_id,
        role_state="core",
        modality_distribution=dist,
        evidence_span=None,  # type: ignore[arg-type]
        coverage_status=coverage if coverage_override is None else coverage_override,
        weight=0.8,
        confidence=0.9,
        policy_version="v1",
        observed_at="2026-01-01T00:00:00Z",
    )
    emergence_v32 = {
        "state": "not_emerging",
        "relation": "renaming",
        "evidence_level": None,
        "policy_version": "emergence-v3.2",
    }
    return PositionTemporalProfile(
        position_id=position_id,
        graph_version_id=graph_version_id,
        release_id=release_id,
        role_observations=(obs,),
        role_transitions=(),
        evolution_events=(),
        emergence_v32=emergence_v32,
        coverage_summary=coverage if coverage_override is None else coverage_override,
        controlled_replay_result=None,
        source_ablation=None,
        catalog_snapshot_id=catalog_snapshot_id,
        source_filter_rules=source_filter_rules,
        actual_time_window_start=actual_time_window_start,
        actual_time_window_end=actual_time_window_end,
        state_policy_version=state_policy_version,
    )


def test_comparison_assembles_result():
    cfg = _config()
    prof_a = _profile("POS_JAVA", "REL-1")
    prof_b = _profile("POS_LLM", "REL-1")
    cmp = TemporalComparison.compute(prof_a, prof_b, cfg)
    assert cmp.comparison_id == "cmp:POS_JAVA:POS_LLM"
    assert cmp.comparability_status == "complete"


def test_both_positions_share_configured_time_window():
    cfg = _config()
    prof_a = _profile("POS_A", "REL-1")
    prof_b = _profile("POS_B", "REL-1")
    cmp = TemporalComparison.compute(prof_a, prof_b, cfg)
    assert cmp.source_config.time_window_start == "2026-01-01"
    assert cmp.source_config.time_window_end == "2026-01-07"


def test_different_releases_do_not_block():
    """Per-position release IDs may differ; release is not a comparability gate."""
    cfg = _config()
    prof_a = _profile("POS_A", "REL-1")
    prof_b = _profile("POS_B", "REL-2")
    cmp = TemporalComparison.compute(prof_a, prof_b, cfg)
    assert cmp.comparability_status == "complete"
    assert not any("release mismatch" in lm for lm in cmp.limitations)


def test_different_graph_versions_do_not_block():
    """Per-position GraphVersion PKs differ; this is expected, not a gate."""
    cfg = _config()
    prof_a = _profile("POS_A", "REL-1", graph_version_id=1)
    prof_b = _profile("POS_B", "REL-1", graph_version_id=5)
    cmp = TemporalComparison.compute(prof_a, prof_b, cfg)
    assert cmp.comparability_status == "complete"
    assert not any("graph_version mismatch" in lm for lm in cmp.limitations)


def test_backend_vs_llm_different_versions_comparable():
    """TEMP-13: BACKEND_ENGINEER vs LLM_ALGORITHM_ENGINEER with different
    graph versions and releases still compares when shared conditions match."""
    cfg = _config()
    prof_backend = _profile(
        "BACKEND_ENGINEER", "REL-BACKEND", graph_version_id=42,
    )
    prof_llm = _profile(
        "LLM_ALGORITHM_ENGINEER", "REL-LLM", graph_version_id=77,
    )
    cmp = TemporalComparison.compute(prof_backend, prof_llm, cfg)
    assert cmp.comparability_status == "complete"
    assert cmp.comparison_id == "cmp:BACKEND_ENGINEER:LLM_ALGORITHM_ENGINEER"


def test_blocked_when_time_window_not_configured():
    cfg = TemporalComparisonSourceConfig(
        time_window_start="",
        time_window_end="",
        catalog_snapshot_id="CAT-v1",
        source_filter_rules=("all",),
        state_policy_version="v1",
    )
    prof_a = _profile("POS_A", "REL-1")
    prof_b = _profile("POS_B", "REL-1")
    cmp = TemporalComparison.compute(prof_a, prof_b, cfg)
    assert cmp.comparability_status == "blocked"


def test_insufficient_evidence_when_coverage_low():
    """When one profile has insufficient_evidence, comparability degrades."""
    cfg = _config()
    # Build a coverage status with insufficient_evidence
    relations_low = [{"modality": "required", "weight": 0.8}]
    dist_low = compute_modality_distribution(relations_low, 5, {"s1"}, {"e1"})
    coverage_low = assess_coverage_status(dist_low, {"s1"}, {"e1"})

    prof_a = _profile("POS_A", "REL-1", coverage_override=coverage_low)
    prof_b = _profile("POS_B", "REL-1")
    cmp = TemporalComparison.compute(prof_a, prof_b, cfg)
    assert cmp.comparability_status == "insufficient_evidence"


def test_blocked_takes_precedence_over_insufficient_evidence():
    """blocked status takes priority over insufficient_evidence."""
    cfg = _config()
    relations_low = [{"modality": "required", "weight": 0.8}]
    dist_low = compute_modality_distribution(relations_low, 5, {"s1"}, {"e1"})
    coverage_low = assess_coverage_status(dist_low, {"s1"}, {"e1"})

    prof_a = _profile("POS_A", "REL-1", coverage_override=coverage_low)
    prof_b = _profile("POS_B", "REL-1", catalog_snapshot_id="CAT-v2")  # catalog mismatch → blocked
    cmp = TemporalComparison.compute(prof_a, prof_b, cfg)
    assert cmp.comparability_status == "blocked"


def test_evolution_event_diff_computed():
    cfg = _config()
    prof_a = _profile("POS_A", "REL-1")
    prof_b = _profile("POS_B", "REL-1")
    cmp = TemporalComparison.compute(prof_a, prof_b, cfg)
    assert "shared" in cmp.evolution_event_diff
    assert "only_position_a" in cmp.evolution_event_diff
    assert "only_position_b" in cmp.evolution_event_diff


def test_role_state_diff_computed():
    cfg = _config()
    prof_a = _profile("POS_A", "REL-1")
    prof_b = _profile("POS_B", "REL-1")
    cmp = TemporalComparison.compute(prof_a, prof_b, cfg)
    assert len(cmp.role_state_diff) == 1  # both have SKILL_TEST


def test_emergence_v32_comparison_handles_both_positions():
    cfg = _config()
    prof_a = _profile("POS_A", "REL-1")
    prof_b = _profile("POS_B", "REL-1")
    cmp = TemporalComparison.compute(prof_a, prof_b, cfg)
    assert "position_a" in cmp.emergence_v32_comparison
    assert "position_b" in cmp.emergence_v32_comparison


def test_evidence_summary_tracks_counts():
    cfg = _config()
    prof_a = _profile("POS_A", "REL-1")
    prof_b = _profile("POS_B", "REL-1")
    cmp = TemporalComparison.compute(prof_a, prof_b, cfg)
    assert cmp.evidence_summary["position_a_events"] == 0
    assert cmp.evidence_summary["position_b_events"] == 0
    assert cmp.evidence_summary["a_role_observations"] == 1
    assert cmp.evidence_summary["b_role_observations"] == 1


def test_deterministic_with_same_inputs():
    cfg = _config()
    p1a = _profile("P1", "R1")
    p1b = _profile("P1", "R1")
    cmp1 = TemporalComparison.compute(p1a, p1b, cfg, comparison_id="test-cmp")
    cmp2 = TemporalComparison.compute(p1a, p1b, cfg, comparison_id="test-cmp")
    assert cmp1.comparison_id == cmp2.comparison_id


def test_policy_version_propagated():
    cfg = _config()
    prof_a = _profile("POS_A", "REL-1")
    prof_b = _profile("POS_B", "REL-1")
    cmp = TemporalComparison.compute(prof_a, prof_b, cfg)
    assert cmp.policy_version == "v1"


# -- TEMP-13: per-profile provenance --


def test_blocked_when_catalog_snapshot_mismatch():
    cfg = _config()
    prof_a = _profile("POS_A", "REL-1", catalog_snapshot_id="CAT-v1")
    prof_b = _profile("POS_B", "REL-1", catalog_snapshot_id="CAT-v2")
    cmp = TemporalComparison.compute(prof_a, prof_b, cfg)
    assert cmp.comparability_status == "blocked"
    assert any("catalog_snapshot mismatch" in lm for lm in cmp.limitations)


def test_blocked_when_time_window_mismatch():
    cfg = _config()
    prof_a = _profile("POS_A", "REL-1",
                      actual_time_window_start="2026-01-01",
                      actual_time_window_end="2026-01-07")
    prof_b = _profile("POS_B", "REL-1",
                      actual_time_window_start="2026-02-01",
                      actual_time_window_end="2026-02-07")
    cmp = TemporalComparison.compute(prof_a, prof_b, cfg)
    assert cmp.comparability_status == "blocked"
    assert any("time_window mismatch" in lm for lm in cmp.limitations)


def test_state_policy_mismatch_does_not_block():
    cfg = _config()
    prof_a = _profile("POS_A", "REL-1", state_policy_version="v1")
    prof_b = _profile("POS_B", "REL-1", state_policy_version="v2")
    cmp = TemporalComparison.compute(prof_a, prof_b, cfg)
    # policy version mismatch is a limitation, not a hard block
    assert cmp.comparability_status == "complete"
    assert any("state_policy_version mismatch" in lm for lm in cmp.limitations)


def test_profile_catalog_contradicts_shared_config():
    cfg = _config()  # catalog_snapshot_id = "CAT-v1"
    prof_a = _profile("POS_A", "REL-1", catalog_snapshot_id="CAT-v9")
    prof_b = _profile("POS_B", "REL-1")
    cmp = TemporalComparison.compute(prof_a, prof_b, cfg)
    assert any("contradicts shared config" in lm for lm in cmp.limitations)


def test_provenance_fields_are_populated():
    prof = _profile("POS_A", "REL-1")
    assert prof.catalog_snapshot_id == "CAT-v1"
    assert prof.actual_time_window_start == "2026-01-01"
    assert prof.actual_time_window_end == "2026-01-07"
    assert prof.state_policy_version == "v1"
    assert prof.source_filter_rules == ("all",)


def test_none_provenance_skips_comparison():
    """When provenance is None (not provided), the check is skipped."""
    cfg = _config()
    prof_a = _profile("POS_A", "REL-1", catalog_snapshot_id=None)
    prof_b = _profile("POS_B", "REL-1", catalog_snapshot_id=None)
    cmp = TemporalComparison.compute(prof_a, prof_b, cfg)
    assert cmp.comparability_status == "complete"
