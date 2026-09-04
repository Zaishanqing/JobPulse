from __future__ import annotations

import pytest

from app.domain.candidate_identity import CandidateIdentitySpec
from app.domain.candidate_profile import CandidateWindowEvidence
from app.domain.candidate_profile_v3 import (
    PROFILE_V3_VERSION,
    CandidateIdentityProfileV3,
    build_profile_v3,
    profile_v3_config_version,
    profile_v3_factor_values,
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


def test_profile_v3_separates_recent_anchor_and_alias() -> None:
    profile = build_profile_v3(
        "cand-1",
        _windows(),
        window_order=("W1", "W2", "W3"),
    )
    assert profile.profile_version == PROFILE_V3_VERSION
    assert profile.recent_window_ids == ("W2", "W3")
    assert "AI Engineer" in profile.title_alias_history
    assert profile.recent_titles == frozenset({"AI Engineer", "AI 产品经理"})


def test_profile_v3_anchor_is_narrowed_to_top_k() -> None:
    windows = [
        CandidateWindowEvidence(
            window_id=f"W{index}",
            skills=frozenset({f"skill-{skill}" for skill in range(1, 41)}),
            responsibilities=frozenset({"work"}),
        )
        for index in range(1, 4)
    ]
    profile = build_profile_v3(
        "cand-1",
        windows,
        {"anchor_top_k_skills": 5, "anchor_support_min_windows": 1},
        window_order=("W1", "W2", "W3"),
    )
    assert (
        len(
            profile.top_skill_anchor_weights(
                {"anchor_top_k_skills": 5, "anchor_support_min_windows": 1}
            )
        )
        <= 5
    )


def test_profile_v3_low_support_anchor_gate() -> None:
    profile = build_profile_v3(
        "cand-1",
        _windows()[:1],
        window_order=("W1",),
    )
    assert profile.support_window_count == 1
    assert profile.anchor_gate() == pytest.approx(0.5)


def test_profile_v3_alias_support_alone_does_not_decide() -> None:
    profile = CandidateIdentityProfileV3(
        candidate_id="cand-alias",
        title_alias_history=frozenset({"AI Engineer"}),
    )
    current = _spec(
        titles={"AI Engineer"},
        skills={"python"},
        responsibilities={"work"},
    )
    factors = profile_v3_factor_values(current, profile)
    components = factors["components"]
    assert components["title_recent"] == 0.0
    assert components["title_anchor"] == 0.0
    assert components["title_alias_support"] > 0.0


def test_profile_v3_roundtrip_is_deterministic() -> None:
    profile = build_profile_v3(
        "cand-1",
        _windows(),
        window_order=("W1", "W2", "W3"),
    )
    restored = CandidateIdentityProfileV3.from_dict(profile.to_dict())
    assert restored.recent_titles == profile.recent_titles
    assert restored.skill_window_frequency == profile.skill_window_frequency
    assert restored.title_alias_history == profile.title_alias_history


def test_profile_v3_config_version_is_stable() -> None:
    assert profile_v3_config_version() == profile_v3_config_version()
