"""Lineage-aware Candidate trajectory reconstruction.

The reader keeps the legacy one-ID trajectory unchanged and additionally
rebuilds structural continuity from persisted CONTINUE / SPLIT / MERGE
relations, so a Candidate can be followed across windows without requiring its
Candidate ID to stay identical.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Sequence

from app.application.lineage_evaluation import LegacyContinuityObservation
from app.domain.candidate_lineage import CandidateLineageRelation


@dataclass(frozen=True)
class TrajectoryStep:
    window_id: str
    candidate_id: str
    title: str = ""

    def to_dict(self) -> Mapping[str, str]:
        return {
            "window_id": self.window_id,
            "candidate_id": self.candidate_id,
            "title": self.title,
        }


@dataclass(frozen=True)
class LineageTrajectoryResult:
    seed_candidate_id: str
    legacy_continuity: tuple[TrajectoryStep, ...]
    lineage_aware_continuity: tuple[TrajectoryStep, ...]
    reachable_candidate_ids: tuple[str, ...]
    relations_used: tuple[
        Mapping[str, str | int | bool | tuple[str, ...]], ...
    ]
    paths: tuple[tuple[TrajectoryStep, ...], ...]

    def to_dict(self) -> Mapping[str, str | int | list | tuple | None]:
        return {
            "seed_candidate_id": self.seed_candidate_id,
            "legacy_continuity": [
                step.to_dict() for step in self.legacy_continuity
            ],
            "lineage_aware_continuity": [
                step.to_dict() for step in self.lineage_aware_continuity
            ],
            "reachable_candidate_ids": list(self.reachable_candidate_ids),
            "relations_used": list(self.relations_used),
            "paths": [
                [step.to_dict() for step in path] for path in self.paths
            ],
        }


def _window_rank(
    window_id: str,
    window_order: Sequence[str] | None,
) -> tuple[int, str]:
    if window_order is not None:
        try:
            return (window_order.index(window_id), window_id)
        except ValueError:
            return (len(window_order), window_id)
    return (0, window_id)


def _steps(
    observations: Sequence[Any],
    candidate_ids: set[str],
    window_order: Sequence[str] | None,
) -> tuple[TrajectoryStep, ...]:
    selected = [
        observation
        for observation in observations
        if getattr(observation, "candidate_id", None) in candidate_ids
    ]
    selected.sort(
        key=lambda item: (
            _window_rank(str(item.window_id), window_order),
            str(item.candidate_id),
        )
    )
    return tuple(
        TrajectoryStep(
            window_id=str(item.window_id),
            candidate_id=str(item.candidate_id),
            title=str(getattr(item, "title", "") or ""),
        )
        for item in selected
    )


def legacy_continuity(
    candidate_id: str,
    observations: Sequence[LegacyContinuityObservation],
    *,
    window_order: Sequence[str] | None = None,
) -> tuple[TrajectoryStep, ...]:
    return _steps(
        observations,
        {candidate_id},
        window_order,
    )


def _reachable_candidate_ids(
    seed_candidate_id: str,
    relations: Sequence[CandidateLineageRelation],
) -> tuple[str, ...]:
    reachable: set[str] = {seed_candidate_id}
    queue = [seed_candidate_id]
    while queue:
        current = queue.pop()
        for relation in relations:
            if current in relation.source_candidate_ids:
                for candidate_id in relation.target_candidate_ids:
                    if candidate_id not in reachable:
                        reachable.add(candidate_id)
                        queue.append(candidate_id)
            if current in relation.target_candidate_ids:
                for candidate_id in relation.source_candidate_ids:
                    if candidate_id not in reachable:
                        reachable.add(candidate_id)
                        queue.append(candidate_id)
    return tuple(sorted(reachable))


def _relations_used(
    seed_candidate_id: str,
    relations: Sequence[CandidateLineageRelation],
) -> tuple[dict[str, Any], ...]:
    reachable = set(_reachable_candidate_ids(seed_candidate_id, relations))
    return tuple(
        {
            "relation_id": relation.relation_id,
            "relation_type": relation.relation_type,
            "source_candidate_ids": sorted(relation.source_candidate_ids),
            "target_candidate_ids": sorted(relation.target_candidate_ids),
            "source_window_id": relation.source_window_id,
            "target_window_id": relation.target_window_id,
            "review_required": relation.review_required,
        }
        for relation in relations
        if (set(relation.source_candidate_ids) & reachable)
        and (set(relation.target_candidate_ids) & reachable)
    )


def _paths(
    seed_candidate_id: str,
    observations: Sequence[Any],
    relations: Sequence[CandidateLineageRelation],
    window_order: Sequence[str] | None,
) -> tuple[tuple[TrajectoryStep, ...], ...]:
    observation_by_key = {
        (str(observation.candidate_id), str(observation.window_id)): observation
        for observation in observations
    }
    seed_observations = sorted(
        [
            observation
            for observation in observations
            if str(observation.candidate_id) == seed_candidate_id
        ],
        key=lambda item: _window_rank(str(item.window_id), window_order),
    )
    if not seed_observations:
        return ()
    start = seed_observations[0]

    def walk(
        candidate_id: str,
        current_window: str,
        seen: frozenset[tuple[str, str]],
        path: tuple[TrajectoryStep, ...],
    ) -> list[tuple[TrajectoryStep, ...]]:
        current = observation_by_key.get((candidate_id, current_window))
        if current is not None:
            step = TrajectoryStep(
                window_id=current_window,
                candidate_id=candidate_id,
                title=str(getattr(current, "title", "") or ""),
            )
            if (candidate_id, current_window) not in seen:
                path = (*path, step)
                seen = seen | {(candidate_id, current_window)}
        outgoing = [
            relation
            for relation in relations
            if candidate_id in relation.source_candidate_ids
            and relation.source_window_id == current_window
            and not relation.review_required
        ]
        if not outgoing:
            return [path]
        results: list[tuple[TrajectoryStep, ...]] = []
        for relation in outgoing:
            for target_candidate_id in relation.target_candidate_ids:
                key = (target_candidate_id, relation.target_window_id)
                if key in seen:
                    continue
                target = observation_by_key.get(key)
                if target is None:
                    continue
                target_step = TrajectoryStep(
                    window_id=relation.target_window_id,
                    candidate_id=target_candidate_id,
                    title=str(getattr(target, "title", "") or ""),
                )
                results.extend(
                    walk(
                        target_candidate_id,
                        relation.target_window_id,
                        seen | {key},
                        (*path, target_step),
                    )
                )
        return results

    return tuple(
        walk(
            seed_candidate_id,
            str(start.window_id),
            frozenset(),
            (),
        )
    )


def reconstruct_lineage_trajectory(
    seed_candidate_id: str,
    observations: Sequence[LegacyContinuityObservation],
    relations: Sequence[CandidateLineageRelation],
    *,
    window_order: Sequence[str] | None = None,
) -> LineageTrajectoryResult:
    legacy = legacy_continuity(
        seed_candidate_id,
        observations,
        window_order=window_order,
    )
    reachable = _reachable_candidate_ids(seed_candidate_id, relations)
    lineage_steps = _steps(observations, set(reachable), window_order)
    return LineageTrajectoryResult(
        seed_candidate_id=seed_candidate_id,
        legacy_continuity=legacy,
        lineage_aware_continuity=lineage_steps,
        reachable_candidate_ids=reachable,
        relations_used=_relations_used(seed_candidate_id, relations),
        paths=_paths(
            seed_candidate_id,
            observations,
            relations,
            window_order,
        ),
    )
