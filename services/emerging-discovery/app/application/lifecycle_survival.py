"""Read-only time-to-event analysis with explicit right censoring."""

from __future__ import annotations

from dataclasses import dataclass

from app.ports.providers import DiscoveryUnitOfWork
from app.ports.records import CandidateLifecycleTrajectoryRecord, LifecycleWindowRecord


EVENT_STATUS = {
    "time_to_incubating": "incubating",
    "time_to_stable": "stable_emerging_role",
    "time_to_dead": "dead",
    "time_to_noise": "noise",
}


@dataclass(frozen=True)
class LifecycleSurvivalResult:
    candidate_id: str
    start_window: str
    event_window: str | None
    duration: int
    event_type: str
    censored: bool
    last_observed_window: str
    observation_end_window: str
    start_run_id: str
    event_run_id: str | None
    observation_end_run_id: str
    start_request_id: str
    algorithm_version: str
    formula_version: str


def analyze_lifecycle_survival(
    trajectories: tuple[CandidateLifecycleTrajectoryRecord, ...],
    windows: tuple[LifecycleWindowRecord, ...],
    *,
    event_type: str | None = None,
) -> tuple[LifecycleSurvivalResult, ...]:
    """Extract actual transition events; absence at observation end is censoring."""
    if event_type is not None and event_type not in EVENT_STATUS:
        raise ValueError(f"unsupported lifecycle survival event type: {event_type}")
    if not windows:
        return ()

    window_by_run = {item.run_id: item for item in windows}
    requested_events = (event_type,) if event_type else tuple(EVENT_STATUS)
    results: list[LifecycleSurvivalResult] = []

    for trajectory in sorted(trajectories, key=lambda item: item.candidate_id):
        candidate_run_ids = {item.run_id for item in trajectory.observations}
        candidate_windows = _ordered_unique_windows(
            tuple(item for item in windows if item.run_id in candidate_run_ids)
        )
        if not candidate_windows:
            continue
        window_index = {
            item.window_id: index for index, item in enumerate(candidate_windows)
        }
        window_by_id = {item.window_id: item for item in candidate_windows}
        observations = sorted(
            trajectory.observations,
            key=lambda item: (
                window_index.get(item.window_id, len(window_index)),
                item.window_id,
                item.id,
            ),
        )
        if not observations:
            continue
        start_observation = observations[0]
        last_observation = observations[-1]
        start = window_by_run.get(start_observation.run_id) or window_by_id.get(
            start_observation.window_id
        )
        if start is None:
            continue
        compatible_windows = _ordered_unique_windows(
            tuple(
                item
                for item in windows
                if item.algorithm_version == start.algorithm_version
                and item.formula_version == start.formula_version
            )
        )
        window_index = {
            item.window_id: index for index, item in enumerate(compatible_windows)
        }
        window_by_id = {item.window_id: item for item in compatible_windows}
        observation_end = compatible_windows[-1]
        for endpoint in requested_events:
            status = EVENT_STATUS[endpoint]
            transition = next(
                (
                    item
                    for item in sorted(
                        trajectory.transitions,
                        key=lambda value: (value.timestamp, value.window_id, value.id),
                    )
                    if item.to_status == status
                ),
                None,
            )
            event = (
                window_by_run.get(transition.run_id)
                if transition is not None and transition.run_id is not None
                else None
            )
            if transition is not None and event is None:
                event = window_by_id.get(transition.window_id)
            occurred = transition is not None and event is not None
            end = event if occurred else observation_end
            results.append(
                LifecycleSurvivalResult(
                    candidate_id=trajectory.candidate_id,
                    start_window=start_observation.window_id,
                    event_window=transition.window_id if occurred else None,
                    duration=max(
                        0,
                        window_index.get(end.window_id, len(compatible_windows) - 1)
                        - window_index.get(start.window_id, 0),
                    ),
                    event_type=endpoint,
                    censored=not occurred,
                    last_observed_window=last_observation.window_id,
                    observation_end_window=end.window_id,
                    start_run_id=start.run_id,
                    event_run_id=event.run_id if occurred else None,
                    observation_end_run_id=end.run_id,
                    start_request_id=start.request_id,
                    algorithm_version=start.algorithm_version,
                    formula_version=start.formula_version,
                )
            )
    return tuple(results)


def _ordered_unique_windows(
    windows: tuple[LifecycleWindowRecord, ...],
) -> tuple[LifecycleWindowRecord, ...]:
    ordered = sorted(windows, key=lambda item: (item.completed_at, item.window_id, item.run_id))
    latest_by_id: dict[str, LifecycleWindowRecord] = {}
    for item in ordered:
        latest_by_id[item.window_id] = item
    return tuple(
        sorted(latest_by_id.values(), key=lambda item: (item.completed_at, item.window_id))
    )


@dataclass(frozen=True)
class EvaluateLifecycleSurvival:
    uow: DiscoveryUnitOfWork

    def execute(
        self,
        *,
        candidate_id: str | None = None,
        event_type: str | None = None,
    ) -> tuple[LifecycleSurvivalResult, ...]:
        with self.uow:
            trajectories = self.uow.candidates.lifecycle_trajectories(candidate_id)
            windows = self.uow.candidates.lifecycle_windows()
        return analyze_lifecycle_survival(trajectories, windows, event_type=event_type)
