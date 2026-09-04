"""Reproducible build watermarks and explicit version comparability gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.domain.value_types import SerializedPayload


@dataclass(frozen=True)
class WatermarkSourceFact:
    source_kind: Literal["published_fact", "legacy_local"]
    source_fact_id: str
    source_fact_version: str
    source_version: str


@dataclass(frozen=True)
class BuildInputWatermark:
    source_facts: tuple[WatermarkSourceFact, ...]
    observation_window_start: str
    observation_window_end: str
    catalog_snapshot_id: str
    catalog_source_version: str
    validation_state: Literal["present", "absent"]
    validation_policy_version: str | None
    mapping_policy_version: str
    aggregation_algorithm_version: str
    normalized_config: SerializedPayload
    config_version: str
    input_coverage: float
    lineage_version: str


@dataclass(frozen=True)
class ComparabilityContext:
    approved_catalog_crosswalk: bool = False
    policy_replay_completed: bool = False
    minimum_input_coverage: float = 0.9


@dataclass(frozen=True)
class ComparabilityDecision:
    comparable: bool
    status: Literal["comparable", "blocked"]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class VersionChangeInputs:
    total_change: float
    input_sample_change: float
    catalog_migration_change: float
    policy_algorithm_change: float
    human_review_change: float


@dataclass(frozen=True)
class VersionChangeAttribution:
    total_change: float
    input_sample_change: float
    catalog_migration_change: float
    policy_algorithm_change: float
    human_review_change: float
    unexplained_residual: float


def create_build_input_watermark(
    *,
    source_facts: tuple[WatermarkSourceFact, ...],
    observation_window_start: str,
    observation_window_end: str,
    catalog_snapshot_id: str,
    catalog_source_version: str,
    validation_policy_version: str | None,
    mapping_policy_version: str,
    aggregation_algorithm_version: str,
    normalized_config: SerializedPayload,
    input_coverage: float,
    validation_state: Literal["present", "absent"] = "present",
) -> BuildInputWatermark:
    identities = [
        (item.source_kind, item.source_fact_id, item.source_fact_version)
        for item in source_facts
    ]
    if len(identities) != len(set(identities)):
        raise ValueError("build watermark source fact identities must be unique")
    required = (
        observation_window_start,
        observation_window_end,
        catalog_snapshot_id,
        mapping_policy_version,
        aggregation_algorithm_version,
    )
    if any(not value.strip() for value in required):
        raise ValueError("build watermark version and window fields cannot be empty")
    if validation_state == "present" and not validation_policy_version:
        raise ValueError("present validation state requires a policy version")
    if validation_state == "absent" and validation_policy_version is not None:
        raise ValueError("absent validation state cannot claim a policy version")
    if input_coverage < 0 or input_coverage > 1:
        raise ValueError("input coverage must be within [0, 1]")
    ordered = tuple(
        sorted(
            source_facts,
            key=lambda item: (
                item.source_kind,
                item.source_fact_id,
                item.source_fact_version,
                item.source_version,
            ),
        )
    )
    config_version = str(normalized_config.get("config_version") or "config-v1")
    return BuildInputWatermark(
        source_facts=ordered,
        observation_window_start=observation_window_start,
        observation_window_end=observation_window_end,
        catalog_snapshot_id=catalog_snapshot_id,
        catalog_source_version=catalog_source_version.lower(),
        validation_state=validation_state,
        validation_policy_version=validation_policy_version,
        mapping_policy_version=mapping_policy_version,
        aggregation_algorithm_version=aggregation_algorithm_version,
        normalized_config=normalized_config,
        config_version=config_version,
        input_coverage=input_coverage,
        lineage_version=f"{catalog_snapshot_id}:{catalog_source_version}",
    )


def compare_build_watermarks(
    left: BuildInputWatermark,
    right: BuildInputWatermark,
    context: ComparabilityContext,
) -> ComparabilityDecision:
    if context.minimum_input_coverage < 0 or context.minimum_input_coverage > 1:
        raise ValueError("minimum input coverage must be within [0, 1]")
    reasons: list[str] = []
    if (
        left.observation_window_start != right.observation_window_start
        or left.observation_window_end != right.observation_window_end
    ):
        reasons.append("observation_window_mismatch")
    catalog_changed = (
        left.catalog_snapshot_id != right.catalog_snapshot_id
        or left.catalog_source_version != right.catalog_source_version
    )
    if catalog_changed and not context.approved_catalog_crosswalk:
        reasons.append("catalog_crosswalk_required")
    policies_changed = (
        left.validation_state != right.validation_state
        or left.validation_policy_version != right.validation_policy_version
        or left.mapping_policy_version != right.mapping_policy_version
        or left.aggregation_algorithm_version != right.aggregation_algorithm_version
        or left.config_version != right.config_version
    )
    if policies_changed and not context.policy_replay_completed:
        reasons.append("policy_replay_required")
    if min(left.input_coverage, right.input_coverage) < context.minimum_input_coverage:
        reasons.append("input_coverage_below_threshold")
    return ComparabilityDecision(
        comparable=not reasons,
        status="comparable" if not reasons else "blocked",
        reasons=tuple(reasons),
    )


def attribute_version_change(inputs: VersionChangeInputs) -> VersionChangeAttribution:
    explained = (
        inputs.input_sample_change
        + inputs.catalog_migration_change
        + inputs.policy_algorithm_change
        + inputs.human_review_change
    )
    return VersionChangeAttribution(
        total_change=round(inputs.total_change, 8),
        input_sample_change=round(inputs.input_sample_change, 8),
        catalog_migration_change=round(inputs.catalog_migration_change, 8),
        policy_algorithm_change=round(inputs.policy_algorithm_change, 8),
        human_review_change=round(inputs.human_review_change, 8),
        unexplained_residual=round(inputs.total_change - explained, 8),
    )
