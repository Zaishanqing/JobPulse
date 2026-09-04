from __future__ import annotations

import pytest

from app.domain.candidate_identity import CandidateIdentitySpec
from app.domain.candidate_profile import (
    PROFILE_V2_VERSION,
    CandidateIdentityProfileV2,
    CandidateWindowEvidence,
    build_profile_v2,
    consecutive_continuity_increment,
    profile_v2_config_version,
    profile_v2_factor_values,
    rebuild_profile_v2_from_observations,
    weighted_jaccard,
)


def _spec(
    *,
    titles: set[str],
    skills: set[str],
    responsibilities: set[str],
) -> CandidateIdentitySpec:
    return CandidateIdentitySpec(
        titles=frozenset(titles),
        skills=frozenset(skills),
        responsibilities=frozenset(responsibilities),
        evidence_titles=frozenset(titles),
        evidence_skills=frozenset(skills),
        evidence_responsibilities=frozenset(responsibilities),
    )


def _windows() -> list[CandidateWindowEvidence]:
    return [
        CandidateWindowEvidence(
            window_id="W1",
            titles=frozenset({"AI Engineer"}),
            skills=frozenset({"python", "rag"}),
            responsibilities=frozenset({"build agents"}),
        ),
        CandidateWindowEvidence(
            window_id="W2",
            titles=frozenset({"AI 产品经理"}),
            skills=frozenset({"python"}),
            responsibilities=frozenset({"design product"}),
        ),
        CandidateWindowEvidence(
            window_id="W3",
            titles=frozenset({"AI Engineer"}),
            skills=frozenset({"python", "rag"}),
            responsibilities=frozenset({"build agents"}),
        ),
    ]


def test_profile_v2_keeps_recent_window_and_weighted_skill_anchor() -> None:
    profile = build_profile_v2(
        "cand-1",
        _windows(),
        window_order=("W1", "W2", "W3"),
    )
    assert profile.profile_version == PROFILE_V2_VERSION
    assert profile.recent_window_ids == ("W2", "W3")
    assert "AI 产品经理" in profile.recent_titles
    assert profile.skill_window_frequency["python"] == 3
    assert profile.skill_support_ratio["rag"] == pytest.approx(2 / 3)
    assert "AI Engineer" in profile.title_alias_history
    assert profile.first_seen_window_id == "W1"
    assert profile.last_seen_window_id == "W3"


def test_weighted_skill_similarity_downweights_rare_union_skill() -> None:
    windows = [
        CandidateWindowEvidence(
            window_id=f"W{index}",
            skills=frozenset({"python", "rag"} if index == 1 else {"python"}),
            responsibilities=frozenset({"work"}),
        )
        for index in range(1, 5)
    ]
    profile = build_profile_v2(
        "cand-1",
        windows,
        window_order=("W1", "W2", "W3", "W4"),
    )
    current = _spec(
        titles={"AI Engineer"},
        skills={"python", "rag"},
        responsibilities={"work"},
    )
    factors = profile_v2_factor_values(current, profile)
    assert 0.0 < factors["skill"] < 1.0


def test_alias_history_support_alone_does_not_decide_title() -> None:
    profile = CandidateIdentityProfileV2(
        candidate_id="cand-alias",
        title_alias_history=frozenset({"AI Engineer"}),
    )
    current = _spec(
        titles={"AI Engineer"},
        skills={"python"},
        responsibilities={"work"},
    )
    factors = profile_v2_factor_values(current, profile)
    assert factors["title"] == 0.0
    assert factors["components"]["title_alias_support"] > 0.0


def test_profile_v2_roundtrip_is_deterministic() -> None:
    profile = build_profile_v2(
        "cand-1",
        _windows(),
        window_order=("W1", "W2", "W3"),
    )
    restored = CandidateIdentityProfileV2.from_dict(profile.to_dict())
    assert restored.profile_version == PROFILE_V2_VERSION
    assert restored.skill_window_frequency == profile.skill_window_frequency
    assert restored.recent_titles == profile.recent_titles
    assert restored.title_alias_history == profile.title_alias_history


def test_rebuild_profile_v2_from_observations() -> None:
    samples_by_id = {
        "s1": {
            "title": "AI Engineer",
            "skills": ["python", "rag"],
            "responsibilities": ["build agents"],
        },
        "s2": {
            "title": "AI 产品经理",
            "skills": ["python"],
            "responsibilities": ["design product"],
        },
    }
    profile = rebuild_profile_v2_from_observations(
        "cand-1",
        [
            ("W1", ["s1"]),
            ("W2", ["s2"]),
            ("W3", ["s1"]),
        ],
        samples_by_id,
        window_order=("W1", "W2", "W3"),
    )
    assert profile.recent_window_ids == ("W2", "W3")
    assert profile.skill_window_frequency["python"] == 3
    assert "AI Engineer" in profile.title_alias_history


def test_weighted_jaccard_uses_intersection_over_union_weights() -> None:
    assert weighted_jaccard({"a": 1.0}, {"a": 1.0, "b": 1.0}) == pytest.approx(0.5)
    assert weighted_jaccard({"a": 1.0, "b": 1.0}, {"a": 1.0, "b": 0.2}) == pytest.approx(
        1.2 / 2.0
    )


def test_profile_v2_config_version_is_stable() -> None:
    assert profile_v2_config_version() == profile_v2_config_version()


def test_consecutive_continuity_only_increments_same_adjacent_eligible() -> None:
    order = ("W1", "W2", "W3")
    assert (
        consecutive_continuity_increment(
            decision="same",
            previous_window_id=None,
            current_window_id="W2",
            window_order=order,
        )
        == 0
    )
    assert (
        consecutive_continuity_increment(
            decision="new",
            previous_window_id="W1",
            current_window_id="W2",
            window_order=order,
        )
        == 0
    )
    assert (
        consecutive_continuity_increment(
            previous_identity_stability=2,
            decision="same",
            previous_window_id="W1",
            current_window_id="W2",
            window_order=order,
        )
        == 3
    )
    assert (
        consecutive_continuity_increment(
            decision="same",
            previous_window_id="W1",
            current_window_id="W3",
            window_order=order,
        )
        == 0
    )
