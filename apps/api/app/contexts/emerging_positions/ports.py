from __future__ import annotations

from collections.abc import Mapping as _Mapping
from typing import Protocol as _Protocol

from app.contexts.emerging_positions.domain import (
    ClusterRecord as _ClusterRecord,
    DefinitionVersionRecord as _DefinitionVersionRecord,
    EmergingRecord as _EmergingRecord,
    ReleaseGateConfig as _ReleaseGateConfig,
    StandardPositionRecord as _StandardPositionRecord,
)
from app.domain.emerging_position import EmergingCandidate as _EmergingCandidate


class DuplicateEmergingProjection(RuntimeError):
    pass


class EmergingPositionRepository(_Protocol):
    def get_cluster(self, cluster_id: str) -> _ClusterRecord | None: ...

    def upsert_formal_experiment_cluster(
        self,
        *,
        cluster_id: str,
        cluster_name: str,
        sample_count: int,
        representative_titles: tuple[str, ...],
        representative_jd_ids: tuple[str, ...],
        discovery_assessment: _Mapping[str, object],
        generated_definition: _Mapping[str, object],
        discovery_run_id: str,
    ) -> bool: ...

    def get(self, emerging_id: str) -> _EmergingRecord | None: ...

    def get_by_cluster(self, cluster_id: str) -> _EmergingRecord | None: ...

    def list(self) -> list[_EmergingRecord]: ...

    def add_candidate(self, candidate: _EmergingCandidate) -> None: ...

    def save_candidate(self, candidate: _EmergingCandidate) -> None: ...

    def delete_candidate(self, emerging_id: str) -> None: ...

    def release_config(self) -> _ReleaseGateConfig: ...

    def get_standard_by_source(self, emerging_id: str) -> _StandardPositionRecord | None: ...

    def add_standard_from(self, candidate: _EmergingCandidate) -> _StandardPositionRecord: ...

    def create_definition_version(
        self, candidate: _EmergingCandidate, actor_id: str
    ) -> _DefinitionVersionRecord: ...

    def list_definition_versions(self, emerging_id: str) -> list[_DefinitionVersionRecord]: ...

    def select_definition_version(
        self, emerging_id: str, version_id: str
    ) -> tuple[_EmergingCandidate, _DefinitionVersionRecord] | None: ...


class EmergingPositionUnitOfWork(_Protocol):
    repository: EmergingPositionRepository

    def __enter__(self) -> "EmergingPositionUnitOfWork": ...

    def __exit__(self, exc_type, exc, traceback) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


__all__ = [
    "DuplicateEmergingProjection",
    "EmergingPositionRepository",
    "EmergingPositionUnitOfWork",
]

del annotations
