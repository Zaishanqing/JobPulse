"""TEMP-05: Controlled replay with 4-factor (D/C/P/H) decomposition.

The four factors under comparison are:

- ``D`` — source facts / data: the ``skill_relations`` / ``responsibilities``
  content of the before and after snapshots.
- ``C`` — catalog snapshot / mappings (``catalog_snapshot_id``,
  ``catalog_source_version``).
- ``P`` — algorithm / policy / config (``validation_policy_version``,
  ``mapping_policy_version``, ``aggregation_algorithm_version``,
  ``normalized_config``).
- ``H`` — human review decisions.

A controlled replay freezes C/P/H to the ``before_watermark`` values and re-runs
the detector on the same before→after snapshot pair.  The frozen values are
injected as ``_replay_*`` config keys, consumed by ``evolution._replay_context``,
and recorded on each event as ``replay_context`` plus ``[replay:...]`` markers on
``config_version`` / ``detector_version``.  Those are provenance/identity markers,
not business results.

The replay diff therefore compares *business content only* (event_type, entities,
confidence, magnitude, reason, metrics) and excludes run metadata
(``config_version``, ``detector_version``, ``replay_context``, ``created_at``,
``event_id``).  ``persisted_differences`` is true only when freezing C/P/H
actually changes the business events.

Reuses BuildInputWatermark, compare_build_watermarks(), and
attribute_version_change() from temporal_analysis.py.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from app.domain.temporal_analysis import (
    BuildInputWatermark,
    VersionChangeInputs,
    attribute_version_change,
)


@dataclass(frozen=True)
class FourFactorDecomposition:
    D_data_change: float
    C_catalog_change: float
    P_policy_algorithm_change: float
    H_human_review_change: float
    unexplained_residual: float


@dataclass(frozen=True)
class ControlledReplayRequest:
    mode: Literal["temporal_evolution", "controlled_replay"]
    before_watermark: BuildInputWatermark
    after_watermark: BuildInputWatermark
    freeze_factors: frozenset[Literal["C", "P", "H"]]
    replay_config_version: str


@dataclass(frozen=True)
class ControlledReplayResult:
    request: ControlledReplayRequest
    before_events: list[dict]
    after_events: list[dict]
    decomposition: FourFactorDecomposition
    event_delta: dict[str, list[dict]]
    persisted_differences: bool
    experimental_replay: bool
    replay_id: str


def build_minimal_closure(
    before_watermark: BuildInputWatermark,
    after_watermark: BuildInputWatermark,
    *,
    freeze_factors: frozenset[Literal["C", "P", "H"]] = frozenset({"C", "P", "H"}),
    replay_config_version: str = "controlled-replay-v1",
) -> ControlledReplayRequest:
    return ControlledReplayRequest(
        mode="controlled_replay",
        before_watermark=before_watermark,
        after_watermark=after_watermark,
        freeze_factors=freeze_factors,
        replay_config_version=replay_config_version,
    )


def decompose_change(
    inputs: VersionChangeInputs,
) -> FourFactorDecomposition:
    attr = attribute_version_change(inputs)
    return FourFactorDecomposition(
        D_data_change=round(attr.input_sample_change, 6),
        C_catalog_change=round(attr.catalog_migration_change, 6),
        P_policy_algorithm_change=round(attr.policy_algorithm_change, 6),
        H_human_review_change=round(attr.human_review_change, 6),
        unexplained_residual=round(attr.unexplained_residual, 6),
    )


def replay_id_for(
    before_watermark: BuildInputWatermark,
    after_watermark: BuildInputWatermark,
    freeze_factors: frozenset[Literal["C", "P", "H"]],
) -> str:
    factors = "".join(sorted(freeze_factors)) if freeze_factors else "none"
    return (
        f"replay:{before_watermark.catalog_snapshot_id}"
        f"->{after_watermark.catalog_snapshot_id}"
        f":freeze_{factors}:v1"
    )


def _build_frozen_config(
    watermark: BuildInputWatermark,
    freeze_factors: frozenset[Literal["C", "P", "H"]],
    base_config: Mapping[str, object],
) -> dict[str, object]:
    """Inject frozen C/P/H watermark values into config overrides.

    Frozen factors are pinned to ``before_watermark`` values and recorded as
    ``_replay_*`` keys.  ``evolution._replay_context`` consumes them to stamp
    ``replay_context`` and ``[replay:...]`` identity markers on each event.

    Note: these keys record *replay identity* (provenance) on the events; they
    do not alter the detector's business thresholds, because snapshot
    reconstruction from source facts is out of scope for the detector-level
    replay.  The replay diff therefore excludes these metadata markers.
    """
    frozen = dict(base_config)
    if "C" in freeze_factors:
        frozen["_replay_catalog_snapshot_id"] = watermark.catalog_snapshot_id
        frozen["_replay_catalog_source_version"] = watermark.catalog_source_version
    if "P" in freeze_factors:
        frozen["_replay_validation_policy_version"] = watermark.validation_policy_version
        frozen["_replay_mapping_policy_version"] = watermark.mapping_policy_version
        frozen["_replay_aggregation_algorithm_version"] = watermark.aggregation_algorithm_version
        if watermark.normalized_config:
            frozen["_replay_normalized_config"] = dict(watermark.normalized_config)
    if "H" in freeze_factors:
        frozen["_replay_human_review_frozen"] = True
    return frozen


# Run metadata that must never count as a business-result difference when
# diffing the normal run against the frozen replay run.
_REPLAY_METADATA_FIELDS = frozenset(
    {
        "event_id",
        "created_at",
        "config_version",
        "detector_version",
        "replay_context",
    }
)


def _business_view(event: Mapping[str, object]) -> dict[str, object]:
    """Return the business-relevant view of an evolution event.

    Excludes run metadata (``config_version``, ``detector_version``,
    ``replay_context``, ``created_at``, ``event_id``) so that a controlled
    replay diff reflects only business-result changes, not config-injection
    artifacts.
    """
    return {
        key: value
        for key, value in event.items()
        if key not in _REPLAY_METADATA_FIELDS
    }


def _event_key(event: Mapping[str, object]) -> tuple[str, str]:
    """Stable business identity for an event.

    ``event_id`` is derived from ``(event_type, source_entities,
    target_entities)`` sort order, so it is a valid business key for matching
    events across the normal and frozen runs.
    """
    return (str(event.get("event_type") or ""), str(event.get("event_id") or ""))


def execute_controlled_replay(
    request: ControlledReplayRequest,
    before_snapshot: Mapping[str, object],
    after_snapshot: Mapping[str, object],
    *,
    position_id: str,
    from_version_id: int,
    to_version_id: int,
    config: Mapping[str, object] | None = None,
) -> ControlledReplayResult:
    from app.domain.evolution import detect_evolution_events

    effective = dict(config or {})

    # Original run: detect events normally using the actual before→after pair
    before_events = detect_evolution_events(
        before_snapshot,
        after_snapshot,
        position_id=position_id,
        from_version_id=from_version_id,
        to_version_id=to_version_id,
        config=effective,
    )

    if request.mode == "controlled_replay":
        # Controlled replay: freeze C/P/H to before_watermark values,
        # then re-run the detector. If differences persist, they are
        # attributable to D (data) rather than C/P/H changes.
        frozen_config = _build_frozen_config(
            request.before_watermark,
            request.freeze_factors,
            effective,
        )
        after_events = detect_evolution_events(
            before_snapshot,
            after_snapshot,
            position_id=position_id,
            from_version_id=from_version_id,
            to_version_id=to_version_id,
            config=frozen_config,
        )
    else:
        # temporal_evolution: compare different version pairs naturally
        after_events = detect_evolution_events(
            before_snapshot,
            after_snapshot,
            position_id=position_id,
            from_version_id=from_version_id,
            to_version_id=to_version_id,
            config=effective,
        )

    # diff events on business content only (run metadata excluded)
    before_views = {_event_key(e): _business_view(e) for e in before_events}
    after_views = {_event_key(e): _business_view(e) for e in after_events}
    before_keys = set(before_views)
    after_keys = set(after_views)

    added = [e for e in after_events if _event_key(e) not in before_keys]
    removed = [e for e in before_events if _event_key(e) not in after_keys]
    modified = [
        e for e in after_events
        if _event_key(e) in before_keys
        and _business_view(e) != before_views[_event_key(e)]
    ]

    event_delta = {
        "added": added,
        "removed": removed,
        "modified": modified,
    }
    persisted = len(added) > 0 or len(removed) > 0 or len(modified) > 0

    # decomposition: if only D varies and differences persist, attribute to D
    if request.mode == "controlled_replay":
        total_change = float(len(added) + len(removed) + len(modified))
        inputs = VersionChangeInputs(
            total_change=total_change,
            input_sample_change=total_change if persisted else 0.0,
            catalog_migration_change=0.0 if "C" in request.freeze_factors else 0.0,
            policy_algorithm_change=0.0 if "P" in request.freeze_factors else 0.0,
            human_review_change=0.0 if "H" in request.freeze_factors else 0.0,
        )
    else:
        inputs = VersionChangeInputs(
            total_change=float(len(added) + len(removed) + len(modified)),
            input_sample_change=float(len(added) + len(removed)),
            catalog_migration_change=0.0,
            policy_algorithm_change=0.0,
            human_review_change=0.0,
        )

    return ControlledReplayResult(
        request=request,
        before_events=before_events,
        after_events=after_events,
        decomposition=decompose_change(inputs),
        event_delta=event_delta,
        persisted_differences=persisted,
        experimental_replay=request.mode == "controlled_replay",
        replay_id=replay_id_for(
            request.before_watermark,
            request.after_watermark,
            request.freeze_factors,
        ),
    )
