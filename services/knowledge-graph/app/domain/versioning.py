"""Version publication and rollback facts and plans."""
from __future__ import annotations

from dataclasses import dataclass

from app.domain.publishing import PublishGateFacts
from app.domain.value_types import SerializedPayload


@dataclass(frozen=True)
class ExistingGraphVersion:
    version_id: int
    version_number: int


@dataclass(frozen=True)
class GraphVersionDependencies:
    published_fact_versions: tuple[str, ...]
    skill_catalog_version: str
    mapping_snapshot_version: str
    normalization_algorithm_version: str
    build_config_version: str
    source_time_window: SerializedPayload


@dataclass(frozen=True)
class PublishVersionFacts:
    run_id: int
    position_id: str
    base_version_id: int | None
    current_version_id: int | None
    previous_version_id: int | None
    previous_version_number: int | None
    existing: ExistingGraphVersion | None
    used_numbers: frozenset[int]
    used_names: frozenset[str]
    snapshot: SerializedPayload
    algorithm_version: str
    dependencies: GraphVersionDependencies
    gate: PublishGateFacts


@dataclass(frozen=True)
class PublishVersionPlan:
    run_id: int
    position_id: str
    version_number: int
    version_name: str
    snapshot: SerializedPayload
    algorithm_version: str
    dependencies: GraphVersionDependencies
    previous_version_id: int | None


@dataclass(frozen=True)
class RollbackVersionFacts:
    source_version_id: int
    position_id: str
    current_version_id: int | None
    latest_version_number: int
    snapshot: SerializedPayload
    algorithm_version: str
    normalization_map_version: str
    dependencies: GraphVersionDependencies


@dataclass(frozen=True)
class RollbackVersionPlan:
    source_version_id: int
    position_id: str
    base_version_id: int | None
    version_number: int
    version_name: str
    snapshot: SerializedPayload
    algorithm_version: str
    normalization_map_version: str
    dependencies: GraphVersionDependencies
