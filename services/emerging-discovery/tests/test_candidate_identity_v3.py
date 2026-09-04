from __future__ import annotations

import pytest

from app.domain.candidate_identity_v3 import (
    DEFAULT_IDENTITY_V3_DECISION_CONFIG,
    DEFAULT_IDENTITY_V3_DELTA_CONFIG,
    compute_identity_v3_delta_rank,
    decide_identity_v3,
    identity_v3_delta_config_version,
    identity_v3_decision_config_version,
)


def _components(**overrides: float) -> dict[str, float]:
    values: dict[str, float] = {
        "title_recent": 0.4,
        "title_anchor": 0.0,
        "responsibility_recent": 0.3,
        "responsibility_anchor": 0.2,
        "skill_recent": 0.3,
        "skill_anchor": 0.2,
        "membership_overlap": 0.0,
        "evidence_confidence": 1.0,
    }
    values.update(overrides)
    return values


def test_identity_v3_delta_keeps_v2_base_by_default() -> None:
    rank = compute_identity_v3_delta_rank(
        v2_base_score=0.30,
        components=_components(),
        temporal_prior=0.0,
    )
    assert rank.v2_base_score == 0.30
    assert rank.final_score == pytest.approx(0.30)


def test_identity_v3_delta_adds_discriminative_skill_bonus() -> None:
    rank = compute_identity_v3_delta_rank(
        v2_base_score=0.30,
        components=_components(
            title_recent=0.5,
            skill_recent=0.6,
        ),
        temporal_prior=0.0,
    )
    assert rank.skill_bonus == 1.0
    assert rank.final_score == pytest.approx(
        0.30 + DEFAULT_IDENTITY_V3_DELTA_CONFIG["discriminative_skill_bonus_weight"]
    )


def test_identity_v3_delta_generic_title_contradiction_guard() -> None:
    rank = compute_identity_v3_delta_rank(
        v2_base_score=0.30,
        components=_components(
            title_recent=0.6,
            skill_recent=0.2,
            responsibility_anchor=0.01,
        ),
        temporal_prior=0.0,
    )
    assert rank.contradiction_penalty == 1.0
    assert rank.final_score < 0.30


def test_identity_v3_delta_left_continuity_bonus() -> None:
    base = compute_identity_v3_delta_rank(
        v2_base_score=0.30,
        components=_components(),
        temporal_prior=0.0,
    )
    with_left = compute_identity_v3_delta_rank(
        v2_base_score=0.30,
        components=_components(),
        temporal_prior=0.0,
        left_candidate_continuity=True,
    )
    assert with_left.final_score > base.final_score
    assert with_left.left_continuity_bonus == 1.0


def test_identity_v3_delta_config_version_is_stable() -> None:
    assert identity_v3_delta_config_version() == identity_v3_delta_config_version()


def test_identity_v3_decision_automatic_same_when_all_gates_pass() -> None:
    decision = decide_identity_v3(
        top1_score=0.40,
        top2_score=0.32,
        top1_continuity_contribution=0.06,
        top1_contradiction_contribution=0.0,
        top1_evidence_confidence=1.0,
        selected_candidate_id="cand-a",
    )
    assert decision.decision == "same"
    assert decision.selected_candidate_id == "cand-a"
    assert decision.top2_gap == pytest.approx(0.08)
    assert decision.review_reason is None


def test_identity_v3_decision_reviews_severe_contradiction() -> None:
    decision = decide_identity_v3(
        top1_score=0.40,
        top2_score=0.32,
        top1_continuity_contribution=0.06,
        top1_contradiction_contribution=0.06,
        top1_evidence_confidence=1.0,
        selected_candidate_id="cand-a",
    )
    assert decision.decision == "review_required"
    assert "severe_contradiction" in decision.decision_basis


def test_identity_v3_decision_reviews_top2_ambiguity() -> None:
    decision = decide_identity_v3(
        top1_score=0.40,
        top2_score=0.38,
        top1_continuity_contribution=0.06,
        top1_contradiction_contribution=0.0,
        top1_evidence_confidence=1.0,
        selected_candidate_id="cand-a",
    )
    assert decision.decision == "review_required"
    assert "top2_ambiguity" in decision.decision_basis


def test_identity_v3_decision_reviews_insufficient_continuity() -> None:
    decision = decide_identity_v3(
        top1_score=0.40,
        top2_score=0.32,
        top1_continuity_contribution=0.0,
        top1_contradiction_contribution=0.0,
        top1_evidence_confidence=1.0,
        selected_candidate_id="cand-a",
        config={
            **DEFAULT_IDENTITY_V3_DECISION_CONFIG,
            "continuity_min_contribution": 0.05,
        },
    )
    assert decision.decision == "review_required"
    assert "insufficient_continuity" in decision.decision_basis


def test_identity_v3_decision_new_when_score_far_below_accept() -> None:
    decision = decide_identity_v3(
        top1_score=0.10,
        top2_score=None,
        top1_continuity_contribution=0.0,
        top1_contradiction_contribution=0.0,
        top1_evidence_confidence=0.5,
        selected_candidate_id="cand-a",
    )
    assert decision.decision == "new"
    assert decision.selected_candidate_id is None


def test_identity_v3_decision_config_version_is_stable() -> None:
    assert (
        identity_v3_decision_config_version()
        == identity_v3_decision_config_version()
    )
