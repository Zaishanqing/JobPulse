from datetime import datetime, timezone

from app.api.mapping import lifecycle_survival_data
from app.application.handlers import QueryDiscovery
from app.application.lifecycle_survival import analyze_lifecycle_survival
from app.ports.records import (
    CandidateLifecycleTrajectoryRecord,
    CandidateObservationRecord,
    CandidateTransitionRecord,
    LifecycleWindowRecord,
)


def _window(index: int) -> LifecycleWindowRecord:
    return LifecycleWindowRecord(
        window_id=f"w{index}",
        run_id=f"run-{index}",
        request_id=f"dataset-{index}",
        algorithm_version="algorithm-v1",
        formula_version="formula-v1",
        completed_at=datetime(2026, index, 1, tzinfo=timezone.utc),
    )


def _observation(index: int, status: str = "weak_signal") -> CandidateObservationRecord:
    return CandidateObservationRecord(
        id=f"observation-{index}",
        candidate_id="candidate-1",
        run_id=f"run-{index}",
        cluster_id=f"cluster-{index}",
        window_id=f"w{index}",
        title="Agent Engineer",
        status=status,
        emergence_score=0.5,
        support_count=2,
        company_count=2,
        identity_similarity=0.9,
        skill_similarity=0.9,
        responsibility_similarity=0.9,
        title_similarity=0.9,
        membership_overlap=0.9,
    )


def _transition(index: int, to_status: str) -> CandidateTransitionRecord:
    return CandidateTransitionRecord(
        id=f"transition-{index}-{to_status}",
        candidate_id="candidate-1",
        from_status="weak_signal",
        to_status=to_status,
        reason="production transition",
        run_id=f"run-{index}",
        window_id=f"w{index}",
        timestamp=datetime(2026, index, 1, tzinfo=timezone.utc),
        transition_version="candidate-lifecycle-v1",
    )


def _trajectory(
    observations: tuple[CandidateObservationRecord, ...],
    transitions: tuple[CandidateTransitionRecord, ...] = (),
) -> CandidateLifecycleTrajectoryRecord:
    return CandidateLifecycleTrajectoryRecord("candidate-1", observations, transitions)


def test_real_transition_is_event_and_other_endpoints_are_right_censored():
    results = analyze_lifecycle_survival(
        (
            _trajectory(
                (_observation(1), _observation(2, "incubating")),
                (_transition(2, "incubating"),),
            ),
        ),
        (_window(1), _window(2), _window(3)),
    )
    incubating = next(item for item in results if item.event_type == "time_to_incubating")
    dead = next(item for item in results if item.event_type == "time_to_dead")

    assert (incubating.event_window, incubating.duration, incubating.censored) == ("w2", 1, False)
    assert incubating.event_run_id == "run-2"
    assert (dead.event_window, dead.duration, dead.censored) == (None, 2, True)
    assert dead.observation_end_window == "w3"


def test_observation_status_and_observation_end_do_not_fabricate_events():
    results = analyze_lifecycle_survival(
        (_trajectory((_observation(1), _observation(2, "dead"))),),
        (_window(1), _window(2)),
    )

    assert all(item.censored for item in results)
    assert all(item.event_window is None for item in results)
    assert {item.event_type for item in results} == {
        "time_to_incubating",
        "time_to_stable",
        "time_to_dead",
        "time_to_noise",
    }


def test_single_window_without_transition_has_zero_duration_and_is_censored():
    result = analyze_lifecycle_survival(
        (_trajectory((_observation(1),)),),
        (_window(1),),
        event_type="time_to_stable",
    )[0]

    assert result.duration == 0
    assert result.censored is True
    assert result.last_observed_window == "w1"


def test_empty_trajectory_and_empty_cohort_do_not_create_samples():
    assert analyze_lifecycle_survival((), (_window(1),)) == ()
    assert analyze_lifecycle_survival((_trajectory(()),), (_window(1),)) == ()


class _Candidates:
    def __init__(self, trajectory, windows):
        self.trajectory = trajectory
        self.windows = windows

    def lifecycle_trajectories(self, candidate_id=None):
        assert candidate_id == "candidate-1"
        return (self.trajectory,)

    def lifecycle_windows(self):
        return self.windows


class _Uow:
    def __init__(self, candidates):
        self.candidates = candidates

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def test_query_application_and_api_dto_match_bottom_layer_result():
    trajectory = _trajectory(
        (_observation(1), _observation(2, "noise")),
        (_transition(2, "noise"),),
    )
    windows = (_window(1), _window(2))
    expected = analyze_lifecycle_survival(
        (trajectory,), windows, event_type="time_to_noise"
    )

    queried = QueryDiscovery(_Uow(_Candidates(trajectory, windows))).lifecycle_survival(
        candidate_id="candidate-1", event_type="time_to_noise"
    )

    assert queried == expected
    assert lifecycle_survival_data(queried[0]) == {
        "candidate_id": "candidate-1",
        "start_window": "w1",
        "event_window": "w2",
        "duration": 1,
        "event_type": "time_to_noise",
        "censored": False,
        "last_observed_window": "w2",
        "observation_end_window": "w2",
        "start_run_id": "run-1",
        "event_run_id": "run-2",
        "observation_end_run_id": "run-2",
        "start_request_id": "dataset-1",
        "algorithm_version": "algorithm-v1",
        "formula_version": "formula-v1",
    }
