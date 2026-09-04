from __future__ import annotations
from app.domain.json_types import FrozenJsonObject as _FrozenJsonObject

from typing import Protocol as _Protocol

from app.contexts.discovery.application_types import (
    ClusterJDRecord as _ClusterJDRecord,
    ClusterProjection as _ClusterProjection,
)
from app.contexts.discovery.contracts import (
    DiscoveryRunRequest as _DiscoveryRunRequest,
    DiscoveryRunResult as _DiscoveryRunResult,
    HistoricalTimeWindow as _HistoricalTimeWindow,
)
from app.contexts.discovery.domain import ReleasedJDFact as _ReleasedJDFact
from app.contexts.tasks import TaskPayload as _TaskPayload, TaskRecord as _TaskRecord


class DiscoveryRepository(_Protocol):
    def list_released_jd_facts(self) -> list[_ReleasedJDFact]: ...

    def list_dataset_jd_facts(self, dataset_id: str) -> list[_ReleasedJDFact]: ...

    def dataset_time_windows(
        self, dataset_id: str
    ) -> tuple[_HistoricalTimeWindow, ...] | None: ...

    def discovery_config(self) -> _FrozenJsonObject: ...

    def get_cluster(self, cluster_id: str) -> _ClusterProjection | None: ...

    def list_clusters(self) -> list[_ClusterProjection]: ...

    def cluster_jds(self, cluster_id: str) -> list[_ClusterJDRecord]: ...

    def delete_cluster(self, cluster_id: str) -> None: ...

    def add_cluster(self, projection: _ClusterProjection) -> None: ...

    def get_task(self, task_id: str) -> _TaskRecord | None: ...

    def record_succeeded_task(
        self,
        *,
        actor_id: str,
        input_payload: _TaskPayload,
        result_payload: _TaskPayload,
        task_id: str,
    ) -> _TaskRecord: ...


class DiscoveryUnitOfWork(_Protocol):
    repository: DiscoveryRepository

    def __enter__(self) -> "DiscoveryUnitOfWork": ...

    def __exit__(self, exc_type, exc, traceback) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class DiscoveryGateway(_Protocol):
    def create_run(self, request: _DiscoveryRunRequest) -> _DiscoveryRunResult: ...


__all__ = ["DiscoveryGateway", "DiscoveryRepository", "DiscoveryUnitOfWork"]

del annotations
