from __future__ import annotations

from dataclasses import dataclass

from app.ports.maintenance import DiscoveryMaintenanceUnitOfWork, MaintenanceAuditRecord


@dataclass(frozen=True)
class PurgeDiscoveryRun:
    uow: DiscoveryMaintenanceUnitOfWork

    def execute(
        self,
        run_id: str,
        *,
        actor: str,
        reason: str,
        supplied_token: str,
    ) -> MaintenanceAuditRecord:
        with self.uow:
            audit = self.uow.maintenance.purge_run(
                run_id,
                actor=actor,
                reason=reason,
                supplied_token=supplied_token,
            )
            self.uow.commit()
            return audit
