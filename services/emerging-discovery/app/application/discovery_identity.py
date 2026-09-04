"""Normalize discovery inputs before execution.

Run identity is assigned by the repository with ``run_id``.  This module only
normalizes values needed by the algorithm and keeps configuration as JSON.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.application.contracts import (
    AlgorithmSelection,
    DiscoveryConfig,
    RunDiscoveryCommand,
    SimilarityThreshold,
)
from app.application.discovery_mapping import normalize_snapshot
from app.application.input_quality import INPUT_PRECHECK_POLICY_VERSION
from app.domain.candidate_lifecycle import DEFAULT_CANDIDATE_LIFECYCLE_CONFIG
from app.domain.discovery import JDSnapshot, PositionReference
from app.domain.germination import DEFAULT_GERMINATION_CONFIG
from app.domain.values import FrozenDict, JsonValue, freeze, thaw


UNORDERED_LIST_FIELDS = frozenset(
    {
        "position_references",
        "required_skills",
        "bonus_skills",
        "business_scenarios",
        "review_flags",
        "aliases",
    }
)


@dataclass(frozen=True)
class DiscoveryIdentityResult:
    snapshots: tuple[JDSnapshot, ...]
    config: DiscoveryConfig
    algorithm: AlgorithmSelection


def normalize_contract(value: JsonValue, field: str | None = None) -> JsonValue:
    if isinstance(value, FrozenDict):
        return FrozenDict({key: normalize_contract(value[key], key) for key in sorted(value)})
    if isinstance(value, tuple):
        normalized = tuple(normalize_contract(item) for item in value)
        if field in UNORDERED_LIST_FIELDS:
            return tuple(sorted(normalized, key=str))
        return normalized
    return value


def normalize_algorithm(name: str) -> AlgorithmSelection:
    normalized = name.strip().casefold()
    if normalized == "emerge_v3_2":
        # Candidate formation keeps the proven multi-view threshold; every
        # formal emergence decision is delegated to the KG v3.2 policy.
        return AlgorithmSelection("emerge_v3_2", name, SimilarityThreshold(0.72))
    raise ValueError(f"unsupported algorithm: {name}")


def discovery_identity(
    command: RunDiscoveryCommand,
    resolved_references: tuple[PositionReference, ...],
    *,
    execution_snapshots: tuple[JDSnapshot, ...] | None = None,
    input_policy_version: str = INPUT_PRECHECK_POLICY_VERSION,
) -> DiscoveryIdentityResult:
    input_snapshots = tuple(
        sorted(
            (normalize_snapshot(item) for item in command.snapshots), key=lambda item: item.jd_id
        )
    )
    snapshots = tuple(
        sorted(
            (
                normalize_snapshot(item)
                for item in (
                    execution_snapshots if execution_snapshots is not None else input_snapshots
                )
            ),
            key=lambda item: item.jd_id,
        )
    )
    merged_config = FrozenDict(
        {
            **DEFAULT_GERMINATION_CONFIG,
            **DEFAULT_CANDIDATE_LIFECYCLE_CONFIG,
            **thaw(
                command.config.values
                if isinstance(command.config, DiscoveryConfig)
                else freeze(command.config)
            ),
            "formula_version": "emerge-v3.2",
        }
    )
    config_value = normalize_contract(freeze(dict(merged_config)))
    if not isinstance(config_value, FrozenDict):
        raise TypeError("normalized discovery config must be a JSON object")
    config = DiscoveryConfig(config_value)
    algorithm = normalize_algorithm(command.algorithm)
    return DiscoveryIdentityResult(
        snapshots=snapshots,
        config=config,
        algorithm=algorithm,
    )
