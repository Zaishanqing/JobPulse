from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class MaintenanceAuditRecord:
    audit_id: int
    run_id: str
    action: str
    status: str
    actor: str
    reason: str
    completed_at: datetime | None


class DiscoveryMaintenancePort(Protocol):
    def purge_run(
        self,
        run_id: str,
        *,
        actor: str,
        reason: str,
        supplied_token: str,
    ) -> MaintenanceAuditRecord: ...


class DiscoveryMaintenanceUnitOfWork(Protocol):
    maintenance: DiscoveryMaintenancePort

    def __enter__(self) -> "DiscoveryMaintenanceUnitOfWork": ...
    def __exit__(self, exc_type, exc, traceback) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
