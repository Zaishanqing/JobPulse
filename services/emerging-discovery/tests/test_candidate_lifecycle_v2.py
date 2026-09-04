from __future__ import annotations

from app.domain.candidate_lifecycle import (
    CANDIDATE_LIFECYCLE_V2_VERSION,
    DEFAULT_CANDIDATE_LIFECYCLE_CONFIG,
    DEFAULT_CANDIDATE_LIFECYCLE_V2_CONFIG,
    LifecycleCoverageState,
    append_lifecycle_observation,
    empty_lifecycle_state_v2,
    record_missing_window,
    trajectory_signals_from_state,
    transition_candidate,
    transition_for_missing_windows,
)


def _v2_config(**overrides: object) -> dict[str, object]:
    return {**DEFAULT_CANDIDATE_LIFECYCLE_V2_CONFIG, **overrides}


def _state_from_trajectories(
    support: tuple[int, ...],
    companies: tuple[int, ...],
    emergence: tuple[float, ...],
    *,
    eligible: bool = True,
):
    state = empty_lifecycle_state_v2()
    for support_count, company_count, emergence_score in zip(
        support, companies, emergence, strict=True
    ):
        state = append_lifecycle_observation(
            state,
            support_count=support_count,
            company_count=company_count,
            emergence_score=emergence_score,
            eligible=eligible,
        )
    return state


def _signals(state):
    return trajectory_signals_from_state(state)


def test_two_early_weak_windows_cannot_become_terminal_noise():
    state = _state_from_trajectories((1, 1), (1, 1), (0.1, 0.1))
    result = transition_candidate(
        "weak_signal",
        supported_window_count=2,
        support_count=1,
        company_count=1,
        emergence_score=0.1,
        identity_similarity=0.9,
        identity_stability=0,
        config=_v2_config(),
        trajectory=_signals(state),
    )

    assert result.changed is False
    assert result.to_status == "weak_signal"
    assert "observing_min_eligible_windows" in result.triggered_rules
    assert result.details["eligible_window_count"] == 2


def test_three_persistent_low_quality_no_growth_windows_can_be_noise():
    state = _state_from_trajectories((1, 1, 1), (1, 1, 1), (0.1, 0.1, 0.1))
    result = transition_candidate(
        "weak_signal",
        supported_window_count=3,
        support_count=1,
        company_count=1,
        emergence_score=0.1,
        identity_similarity=0.9,
        identity_stability=0,
        config=_v2_config(),
        trajectory=_signals(state),
    )

    assert result.changed is True
    assert result.to_status == "noise"
    assert "persistent_low_quality" in result.triggered_rules


def test_support_or_company_growth_blocks_noise():
    growing_support = _state_from_trajectories(
        (1, 1, 2), (1, 1, 1), (0.1, 0.1, 0.1)
    )
    support_result = transition_candidate(
        "weak_signal",
        supported_window_count=3,
        support_count=2,
        company_count=1,
        emergence_score=0.1,
        identity_similarity=0.9,
        identity_stability=0,
        config=_v2_config(),
        trajectory=_signals(growing_support),
    )
    growing_company = _state_from_trajectories(
        (1, 1, 1), (1, 1, 2), (0.1, 0.1, 0.1)
    )
    company_result = transition_candidate(
        "weak_signal",
        supported_window_count=3,
        support_count=1,
        company_count=2,
        emergence_score=0.1,
        identity_similarity=0.9,
        identity_stability=0,
        config=_v2_config(),
        trajectory=_signals(growing_company),
    )

    assert support_result.to_status != "noise"
    assert company_result.to_status != "noise"
    assert "growth_observed" in support_result.triggered_rules
    assert "growth_observed" in company_result.triggered_rules


def test_emergence_rise_blocks_noise():
    state = _state_from_trajectories((1, 1, 1), (1, 1, 1), (0.1, 0.1, 0.31))
    result = transition_candidate(
        "weak_signal",
        supported_window_count=3,
        support_count=1,
        company_count=1,
        emergence_score=0.31,
        identity_similarity=0.9,
        identity_stability=0,
        config=_v2_config(),
        trajectory=_signals(state),
    )

    assert result.to_status != "noise"
    assert "emergence_not_low" in result.triggered_rules


def test_coverage_insufficient_does_not_accumulate_missing_strike():
    coverage = LifecycleCoverageState(
        window_id="w2",
        valid=False,
        source_count=0,
        company_count=0,
        eligible_jd_count=0,
        reasons=("source_coverage_insufficient",),
    )
    state = record_missing_window(
        empty_lifecycle_state_v2(),
        coverage_state=coverage,
        config=_v2_config(),
    )
    result = transition_for_missing_windows(
        "weak_signal",
        int(state["missed_eligible_windows"]),
        _v2_config(),
        coverage_state=coverage,
    )

    assert state["missed_eligible_windows"] == 0
    assert state["missing_events"][0]["reason"] == "INSUFFICIENT_COVERAGE"
    assert result.changed is False
    assert result.to_status == "weak_signal"


def test_coverage_recovers_then_missing_can_be_judged():
    state = _state_from_trajectories((1,), (1,), (0.1,))
    insufficient = LifecycleCoverageState(
        window_id="w2",
        valid=False,
        reasons=("eligible_jd_volume_insufficient",),
    )
    state = record_missing_window(
        state,
        coverage_state=insufficient,
        config=_v2_config(),
    )
    assert state["missed_eligible_windows"] == 0

    valid_1 = LifecycleCoverageState(
        window_id="w3",
        valid=True,
        source_count=2,
        company_count=2,
        eligible_jd_count=5,
    )
    state = record_missing_window(
        state,
        coverage_state=valid_1,
        config=_v2_config(),
    )
    valid_2 = LifecycleCoverageState(
        window_id="w4",
        valid=True,
        source_count=2,
        company_count=2,
        eligible_jd_count=5,
    )
    state = record_missing_window(
        state,
        coverage_state=valid_2,
        config=_v2_config(),
    )
    result = transition_for_missing_windows(
        "weak_signal",
        int(state["missed_eligible_windows"]),
        _v2_config(),
        coverage_state=valid_2,
    )

    assert state["missed_eligible_windows"] == 2
    assert result.changed is True
    assert result.to_status == "dead"


def test_legacy_lifecycle_replays_exactly():
    first = transition_candidate(
        "weak_signal",
        supported_window_count=2,
        support_count=1,
        company_count=1,
        emergence_score=0.1,
        identity_similarity=0.9,
        identity_stability=0,
        config=DEFAULT_CANDIDATE_LIFECYCLE_CONFIG,
    )
    second = transition_candidate(
        "weak_signal",
        supported_window_count=2,
        support_count=1,
        company_count=1,
        emergence_score=0.1,
        identity_similarity=0.9,
        identity_stability=0,
        config=DEFAULT_CANDIDATE_LIFECYCLE_CONFIG,
    )
    dead = transition_for_missing_windows(
        "weak_signal",
        2,
        DEFAULT_CANDIDATE_LIFECYCLE_CONFIG,
    )

    assert first == second
    assert first.to_status == "noise"
    assert first.reason == second.reason
    assert dead.to_status == "dead"
    assert dead.reason == (
        "no candidate support for 2 consecutive windows (threshold 2)"
    )


def test_v2_transition_reason_is_reproducible():
    state = _state_from_trajectories((1, 1, 1), (1, 1, 1), (0.1, 0.1, 0.1))
    config = _v2_config()
    first = transition_candidate(
        "weak_signal",
        supported_window_count=3,
        support_count=1,
        company_count=1,
        emergence_score=0.1,
        identity_similarity=0.9,
        identity_stability=0,
        config=config,
        trajectory=_signals(state),
    )
    second = transition_candidate(
        "weak_signal",
        supported_window_count=3,
        support_count=1,
        company_count=1,
        emergence_score=0.1,
        identity_similarity=0.9,
        identity_stability=0,
        config=config,
        trajectory=_signals(state),
    )

    assert first.to_status == second.to_status == "noise"
    assert first.reason == second.reason
    assert dict(first.details) == dict(second.details)
    assert first.details["policy_version"] == CANDIDATE_LIFECYCLE_V2_VERSION
    assert first.details["ema_emergence"] == 0.1
    assert first.details["delta_support"] == 0
    assert first.details["delta_emergence"] == 0.0
