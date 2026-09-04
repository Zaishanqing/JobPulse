from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

from sqlalchemy import Select, case, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.domain.analysis_run import AnalysisRun, AnalysisRunLog, AnalysisRunStatus, NewAnalysisRun
from app.infrastructure.models import AnalysisRunLogModel, AnalysisRunModel
from app.ports.repository import IdempotencyConflict


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class SqlAlchemyAnalysisRunRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    @staticmethod
    def _domain(row: AnalysisRunModel) -> AnalysisRun:
        return AnalysisRun(
            id=row.id,
            contract_version=row.contract_version,
            request_id=row.request_id,
            idempotency_key=row.idempotency_key,
            status=AnalysisRunStatus(row.status),
            window_start=_utc(row.window_start),
            window_end=_utc(row.window_end),
            data_sources=tuple(row.data_sources),
            weights=row.weights,
            algorithm_version=row.algorithm_version,
            formula_version=row.formula_version,
            run_type=row.run_type,
            run_payload=row.run_payload or {},
            attempt_count=row.attempt_count,
            max_attempts=row.max_attempts,
            cancel_requested=row.cancel_requested,
            error_message=row.error_message,
            created_at=_utc(row.created_at),
            updated_at=_utc(row.updated_at),
            started_at=_utc(row.started_at),
            completed_at=_utc(row.completed_at),
        )

    @staticmethod
    def _identities(command: NewAnalysisRun) -> Select[tuple[AnalysisRunModel]]:
        predicates = [AnalysisRunModel.request_id == command.request_id]
        if command.idempotency_key:
            predicates.append(AnalysisRunModel.idempotency_key == command.idempotency_key)
        return select(AnalysisRunModel).where(or_(*predicates)).order_by(AnalysisRunModel.created_at)

    def _existing(self, session: Session, command: NewAnalysisRun) -> AnalysisRunModel | None:
        rows = list(session.scalars(self._identities(command)))
        if len({row.id for row in rows}) > 1:
            raise IdempotencyConflict(
                identity_keys=["request_id", "idempotency_key"],
                existing_run_id=None,
                reason="request_id and idempotency_key identify different analysis runs",
            )
        return rows[0] if rows else None

    def create_or_get(self, command: NewAnalysisRun, *, max_attempts: int) -> AnalysisRun:
        with self.sessions() as session:
            existing = self._existing(session, command)
            if existing:
                return self._domain(existing)
            row = AnalysisRunModel(
                contract_version=command.contract_version,
                request_id=command.request_id,
                idempotency_key=command.idempotency_key,
                window_start=command.window_start,
                window_end=command.window_end,
                data_sources=list(command.data_sources),
                weights=dict(command.weights),
                algorithm_version=command.algorithm_version,
                formula_version=command.formula_version,
                run_type=command.run_type,
                run_payload=dict(command.run_payload or {}),
                position_id=(command.run_payload or {}).get("position_id"),
                graph_version=(command.run_payload or {}).get("graph_version"),
                config_version=(command.run_payload or {}).get("config_version"),
                max_attempts=max_attempts,
            )
            session.add(row)
            try:
                session.flush()
                session.add(
                    AnalysisRunLogModel(
                        run_id=row.id,
                        level="info",
                        event="created",
                        message="analysis run accepted",
                    )
                )
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = self._existing(session, command)
                if existing is None:
                    raise
                return self._domain(existing)
            return self._domain(row)

    def get(self, run_id: str) -> AnalysisRun | None:
        with self.sessions() as session:
            row = session.get(AnalysisRunModel, run_id)
            return self._domain(row) if row else None

    def logs(self, run_id: str) -> list[AnalysisRunLog]:
        with self.sessions() as session:
            rows = session.scalars(
                select(AnalysisRunLogModel)
                .where(AnalysisRunLogModel.run_id == run_id)
                .order_by(AnalysisRunLogModel.id)
            )
            return [
                AnalysisRunLog(
                    id=row.id,
                    run_id=row.run_id,
                    level=row.level,
                    event=row.event,
                    message=row.message,
                    details=row.details,
                    created_at=_utc(row.created_at),
                )
                for row in rows
            ]

    def cancel(self, run_id: str) -> AnalysisRun | None:
        now = datetime.now(timezone.utc)
        with self.sessions.begin() as session:
            row = session.get(AnalysisRunModel, run_id)
            if row is None:
                return None
            if row.status == "pending":
                row.status = "cancelled"
                row.completed_at = now
            elif row.status == "running":
                row.cancel_requested = True
            row.updated_at = now
            session.add(AnalysisRunLogModel(run_id=run_id, level="info", event="cancel_requested", message="cancellation requested"))
            session.flush()
            return self._domain(row)

    def claim(self, worker_id: str, *, now: datetime, lease: timedelta) -> AnalysisRun | None:
        expires = now + lease
        with self.sessions.begin() as session:
            candidate = (
                select(AnalysisRunModel.id)
                .where(AnalysisRunModel.status == "pending", AnalysisRunModel.available_at <= now)
                .order_by(AnalysisRunModel.created_at, AnalysisRunModel.id)
                .limit(1)
            )
            candidate = candidate.with_for_update(skip_locked=True)
            run_id = session.scalar(candidate)
            if run_id is None:
                return None
            result = session.execute(
                update(AnalysisRunModel)
                .where(AnalysisRunModel.id == run_id, AnalysisRunModel.status == "pending")
                .values(
                    status="running",
                    lease_owner=worker_id,
                    lease_expires_at=expires,
                    attempt_count=AnalysisRunModel.attempt_count + 1,
                    started_at=case((AnalysisRunModel.started_at.is_(None), now), else_=AnalysisRunModel.started_at),
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                return None
            session.add(AnalysisRunLogModel(run_id=run_id, level="info", event="claimed", message="run claimed", details={"worker_id": worker_id}))
            session.flush()
            return self._domain(session.get(AnalysisRunModel, run_id))

    def renew_lease(self, run_id: str, worker_id: str, *, until: datetime) -> bool:
        with self.sessions.begin() as session:
            result = session.execute(update(AnalysisRunModel).where(AnalysisRunModel.id == run_id, AnalysisRunModel.status == "running", AnalysisRunModel.lease_owner == worker_id).values(lease_expires_at=until, updated_at=datetime.now(timezone.utc)))
            return result.rowcount == 1

    def succeed(
        self,
        run_id: str,
        worker_id: str,
        result_summary: Mapping[str, int] | None = None,
    ) -> bool:
        now = datetime.now(timezone.utc)
        with self.sessions.begin() as session:
            row = session.scalar(select(AnalysisRunModel).where(AnalysisRunModel.id == run_id, AnalysisRunModel.status == "running", AnalysisRunModel.lease_owner == worker_id))
            if row is None:
                return False
            row.status = "cancelled" if row.cancel_requested else "succeeded"
            row.completed_at = now
            row.updated_at = now
            row.lease_owner = None
            row.lease_expires_at = None
            event = "cancelled" if row.cancel_requested else "succeeded"
            details = dict(result_summary or {})
            if row.cancel_requested:
                message = "analysis run cancelled after execution"
            elif any(details.values()):
                message = "analysis run succeeded"
            else:
                message = "analysis run succeeded with no generated result records"
            session.add(
                AnalysisRunLogModel(
                    run_id=run_id,
                    level="info",
                    event=event,
                    message=message,
                    details=details,
                )
            )
            return True

    def fail(self, run_id: str, worker_id: str, error: str, *, retry_at: datetime) -> bool:
        now = datetime.now(timezone.utc)
        with self.sessions.begin() as session:
            row = session.scalar(select(AnalysisRunModel).where(AnalysisRunModel.id == run_id, AnalysisRunModel.status == "running", AnalysisRunModel.lease_owner == worker_id))
            if row is None:
                return False
            if row.cancel_requested:
                row.status = "cancelled"
                row.completed_at = now
                event = "cancelled"
            elif row.attempt_count < row.max_attempts:
                row.status = "pending"
                row.available_at = retry_at
                event = "retry_scheduled"
            else:
                row.status = "failed"
                row.completed_at = now
                event = "failed"
            row.error_message = error
            row.updated_at = now
            row.lease_owner = None
            row.lease_expires_at = None
            session.add(AnalysisRunLogModel(run_id=run_id, level="error", event=event, message=error))
            return True

    def recover_expired(self, *, now: datetime) -> int:
        with self.sessions.begin() as session:
            query = select(AnalysisRunModel).where(AnalysisRunModel.status == "running", AnalysisRunModel.lease_expires_at < now)
            query = query.with_for_update(skip_locked=True)
            rows = list(session.scalars(query))
            for row in rows:
                if row.cancel_requested:
                    row.status = "cancelled"
                    row.completed_at = now
                elif row.attempt_count >= row.max_attempts:
                    row.status = "failed"
                    row.completed_at = now
                    row.error_message = "worker lease expired after maximum attempts"
                else:
                    row.status = "pending"
                    row.available_at = now
                row.lease_owner = None
                row.lease_expires_at = None
                row.updated_at = now
                session.add(AnalysisRunLogModel(run_id=row.id, level="warning", event="lease_expired", message="expired worker lease recovered"))
            return len(rows)
