"""Tests for TEMP-02: Skill role state machine with hysteresis."""

from __future__ import annotations

import pytest

from app.domain.skill_role_state import (
    ModalityDistribution,
    SkillRoleObservation,
    SkillRoleState,
    _classify_transition,
    assess_coverage_status,
    classify_role_state,
    compute_modality_distribution,
    detect_role_transition,
    produce_role_observation,
)


def _make_dist(
    required_share: float = 0.0,
    bonus_share: float = 0.0,
    total_coverage: float = 0.0,
    jd_count: int = 3,
    source_count: int = 2,
    enterprise_count: int = 2,
) -> ModalityDistribution:
    return ModalityDistribution(
        required_share=required_share,
        bonus_share=bonus_share,
        total_coverage=total_coverage or (required_share + bonus_share),
        independent_jd_count=jd_count,
        independent_source_count=source_count,
        independent_enterprise_count=enterprise_count,
    )


def _make_obs(
    position_id: str = "POS_TEST",
    skill_id: str = "SKILL_TEST",
    role_state: SkillRoleState = "not_observed",
    required_share: float = 0.0,
) -> SkillRoleObservation:
    return SkillRoleObservation(
        position_id=position_id,
        skill_id=skill_id,
        release_id="REL-1",
        role_state=role_state,
        modality_distribution=_make_dist(required_share=required_share),
        evidence_span=None,  # type: ignore[arg-type]
        coverage_status=assess_coverage_status(
            _make_dist(required_share=required_share),
            {"s1", "s2"}, {"e1", "e2"},
        ),
        weight=required_share,
        confidence=0.8,
        policy_version="skill-role-state-v1",
        observed_at="2026-01-01T00:00:00Z",
    )


# ── State classification ──

def test_not_observed_when_absent():
    dist = _make_dist(required_share=0.0, total_coverage=0.0)
    coverage = assess_coverage_status(dist, set(), set())
    state = classify_role_state(dist, coverage, None)
    assert state == "not_observed"


def test_emerging_with_low_required_share():
    # 0.16 sits above the emerging entry threshold (0.15) but below bonus (0.20)
    dist = _make_dist(required_share=0.16, total_coverage=0.16)
    coverage = assess_coverage_status(dist, {"s1", "s2"}, {"e1", "e2"})
    state = classify_role_state(dist, coverage, None)
    assert state == "emerging"


def test_bonus_with_medium_required_share():
    dist = _make_dist(required_share=0.30, total_coverage=0.35)
    coverage = assess_coverage_status(dist, {"s1", "s2"}, {"e1", "e2"})
    state = classify_role_state(dist, coverage, None)
    assert state == "bonus"


def test_required_with_broad_coverage():
    dist = _make_dist(required_share=0.55, total_coverage=0.60)
    coverage = assess_coverage_status(dist, {"s1", "s2"}, {"e1", "e2"})
    state = classify_role_state(dist, coverage, None)
    assert state == "required"


def test_core_with_high_required_share():
    dist = _make_dist(required_share=0.80, total_coverage=0.85)
    coverage = assess_coverage_status(dist, {"s1", "s2", "s3"}, {"e1", "e2", "e3"})
    state = classify_role_state(dist, coverage, None)
    assert state == "core"


def test_declining_on_large_drop():
    dist = _make_dist(required_share=0.03, total_coverage=0.05)
    coverage = assess_coverage_status(dist, {"s1", "s2"}, {"e1", "e2"})
    state = classify_role_state(
        dist, coverage, "required",
        previous_weight=0.55,
    )
    assert state == "declining"


def test_not_observed_is_not_disappeared():
    dist = _make_dist(required_share=0.0, total_coverage=0.0)
    coverage = assess_coverage_status(dist, set(), set())
    state = classify_role_state(dist, coverage, "core", absent_window_count=1)
    assert state == "not_observed"


def test_retired_after_consecutive_absence():
    dist = _make_dist(required_share=0.0, total_coverage=0.0)
    coverage = assess_coverage_status(dist, set(), set())
    state = classify_role_state(dist, coverage, "core", absent_window_count=2)
    assert state == "retired"


def test_hysteresis_prevents_jitter_upward():
    # oscillating around 0.20 (emerging→bonus upper threshold)
    dist = _make_dist(required_share=0.19, total_coverage=0.19)
    coverage = assess_coverage_status(dist, {"s1", "s2"}, {"e1", "e2"})
    state = classify_role_state(dist, coverage, "emerging", absent_window_count=0)
    assert state == "emerging"  # stays in emerging, did not cross 0.20


def test_hysteresis_prevents_jitter_downward():
    # from bonus, share drops to 0.38 (bonus→emerging lower=0.12, so still bonus)
    dist = _make_dist(required_share=0.38, total_coverage=0.40)
    coverage = assess_coverage_status(dist, {"s1", "s2"}, {"e1", "e2"})
    state = classify_role_state(dist, coverage, "bonus", absent_window_count=0)
    assert state == "bonus"


def test_insufficient_evidence_data_state():
    dist = _make_dist(required_share=0.50, total_coverage=0.50, jd_count=1, source_count=1, enterprise_count=1)
    coverage = assess_coverage_status(dist, {"s1"}, {"e1"})
    assert coverage.data_state == "insufficient_evidence"


def test_source_concentrated_data_state():
    dist = _make_dist(required_share=0.30, total_coverage=0.30, jd_count=5, source_count=1, enterprise_count=1)
    coverage = assess_coverage_status(dist, {"s1"}, {"e1"})
    assert coverage.data_state == "source_concentrated"


def test_modality_distribution_computation():
    relations = [
        {"modality": "required", "weight": 0.8},
        {"modality": "required", "weight": 0.7},
        {"modality": "bonus", "weight": 0.4},
    ]
    dist = compute_modality_distribution(relations, 10, {"s1", "s2"}, {"e1", "e2"})
    assert dist.required_share == 0.2
    assert dist.bonus_share == 0.1
    assert dist.total_coverage == 0.3


def test_insufficient_evidence_blocks_unreliable_role():
    dist = _make_dist(required_share=0.80, total_coverage=0.80, jd_count=1, source_count=1, enterprise_count=1)
    coverage = assess_coverage_status(dist, {"s1"}, {"e1"})
    state = classify_role_state(dist, coverage, None)
    assert state == "not_observed"  # coverage insufficient → not_observed


def test_transition_entry_from_none():
    before = None
    after = _make_obs(role_state="emerging", required_share=0.12)
    t = detect_role_transition(before, after)
    assert t.transition_type == "entry"


def test_transition_exit_to_not_observed():
    before = _make_obs(role_state="emerging", required_share=0.10)
    after = _make_obs(role_state="not_observed", required_share=0.0)
    t = detect_role_transition(before, after)
    assert t.transition_type == "exit"


def test_transition_promotion():
    before = _make_obs(role_state="bonus", required_share=0.30)
    after = _make_obs(role_state="required", required_share=0.55)
    t = detect_role_transition(before, after)
    assert t.transition_type == "promotion"


def test_transition_demotion():
    before = _make_obs(role_state="required", required_share=0.55)
    after = _make_obs(role_state="bonus", required_share=0.30)
    t = detect_role_transition(before, after)
    assert t.transition_type == "demotion"


def test_transition_consolidation():
    before = _make_obs(role_state="required", required_share=0.60)
    after = _make_obs(role_state="core", required_share=0.80)
    t = detect_role_transition(before, after)
    assert t.transition_type == "consolidation"


def test_reactivation_from_retired():
    before = _make_obs(role_state="retired", required_share=0.0)
    after = _make_obs(role_state="emerging", required_share=0.12)
    t = detect_role_transition(before, after)
    assert t.transition_type == "reactivation"


def test_reactivation_from_declining():
    before = _make_obs(role_state="declining", required_share=0.03)
    after = _make_obs(role_state="bonus", required_share=0.30)
    t = detect_role_transition(before, after)
    assert t.transition_type == "reactivation"


def test_declining_maintained_within_band():
    dist = _make_dist(required_share=0.08, total_coverage=0.10)
    coverage = assess_coverage_status(dist, {"s1", "s2"}, {"e1", "e2"})
    state = classify_role_state(
        dist, coverage, "declining",
        previous_weight=0.20,
    )
    # delta = -0.12, which is > -0.15, so NOT declining by threshold
    # But since previous was declining, check recovery threshold -0.08
    # -0.12 < -0.08 so stays declining
    assert state == "declining"


def test_declining_recovers_when_above_recovery():
    # delta = -0.04 > recovery_threshold (-0.08), and share is above the
    # emerging entry threshold (0.15), so it recovers to emerging
    dist = _make_dist(required_share=0.16, total_coverage=0.16)
    coverage = assess_coverage_status(dist, {"s1", "s2"}, {"e1", "e2"})
    state = classify_role_state(
        dist, coverage, "declining",
        previous_weight=0.20,
    )
    assert state == "emerging"


def test_reproducibility():
    dist = _make_dist(required_share=0.35, total_coverage=0.40)
    coverage = assess_coverage_status(dist, {"s1", "s2"}, {"e1", "e2"})
    s1 = classify_role_state(dist, coverage, "emerging")
    s2 = classify_role_state(dist, coverage, "emerging")
    assert s1 == s2


def test_produce_role_observation_with_config():
    relations = [
        {"modality": "required", "weight": 0.8},
        {"modality": "bonus", "weight": 0.4},
    ]
    obs = produce_role_observation(
        "POS_TEST", "SKILL_JAVA", "REL-1", 1,
        "2026-01-01", "2026-01-07",
        "CATALOG-v1", "wm-config-v1",
        relations, 5,
        {"s1", "s2"}, {"e1", "e2"},
    )
    assert obs.position_id == "POS_TEST"
    assert obs.skill_id == "SKILL_JAVA"
    assert obs.policy_version == "skill-role-state-v1"
    assert obs.evidence_span.graph_version_id == 1
    assert obs.evidence_span.catalog_snapshot_id == "CATALOG-v1"


def test_evidence_span_populated():
    relations = [{"modality": "required", "weight": 0.9}]
    obs = produce_role_observation(
        "P1", "S1", "REL-2", 3,
        "2026-02-01", "2026-02-07",
        "CAT-v2", "cfg-v2",
        relations, 3,
        {"s1", "s2"}, {"e1", "e2"},
    )
    span = obs.evidence_span
    assert span.release_id == "REL-2"
    assert span.graph_version_id == 3
    assert span.observation_window_start == "2026-02-01"
    assert span.observation_window_end == "2026-02-07"
    assert span.sample_count == 1


def test_config_version_applied():
    relations = [{"modality": "required", "weight": 0.8}]
    custom = {"policy_version": "custom-v3", "hysteresis": DEFAULT_HYSTERESIS, "coverage": DEFAULT_COVERAGE, "confidence": DEFAULT_CONFIDENCE}
    obs = produce_role_observation(
        "P1", "S1", "REL-1", 1,
        "2026-01-01", "2026-01-07",
        "CAT-v1", "cfg-v1",
        relations, 3,
        {"s1", "s2"}, {"e1", "e2"},
        config=custom,
    )
    assert obs.policy_version == "custom-v3"


# Reuse default config for custom test
from app.domain.skill_role_state import DEFAULT_ROLE_STATE_CONFIG
DEFAULT_HYSTERESIS = DEFAULT_ROLE_STATE_CONFIG["hysteresis"]
DEFAULT_COVERAGE = DEFAULT_ROLE_STATE_CONFIG["coverage"]
DEFAULT_CONFIDENCE = DEFAULT_ROLE_STATE_CONFIG["confidence"]


def test_no_change_transition():
    before = _make_obs(role_state="core", required_share=0.80)
    after = _make_obs(role_state="core", required_share=0.81)
    t = detect_role_transition(before, after)
    assert t.transition_type == "no_change"


def test_evidence_delta_computed():
    before = _make_obs(role_state="bonus", required_share=0.30)
    after = _make_obs(role_state="required", required_share=0.55)
    t = detect_role_transition(before, after)
    assert "required_share_delta" in t.evidence_delta
    assert t.evidence_delta["required_share_delta"] == pytest.approx(0.25)


def test_not_observed_retained_when_weight_barely_changes():
    """coverage 恢复但权重几乎没变时，not_observed 技能不应"新兴"（coverage bounce 抑制）。"""
    dist = _make_dist(required_share=0.182, jd_count=10, source_count=3, enterprise_count=3)
    coverage = assess_coverage_status(dist, {"s1", "s2", "s3"}, {"e1", "e2", "e3"})
    state = classify_role_state(
        dist, coverage, "not_observed",
        previous_weight=0.174,
    )
    assert state == "not_observed"


def test_not_observed_emerges_when_weight_changes_materially():
    """not_observed 技能权重发生实质变化时仍应正常进入有意义状态。"""
    dist = _make_dist(required_share=0.35, jd_count=10, source_count=3, enterprise_count=3)
    coverage = assess_coverage_status(dist, {"s1", "s2", "s3"}, {"e1", "e2", "e3"})
    state = classify_role_state(
        dist, coverage, "not_observed",
        previous_weight=0.15,
    )
    assert state == "bonus"
