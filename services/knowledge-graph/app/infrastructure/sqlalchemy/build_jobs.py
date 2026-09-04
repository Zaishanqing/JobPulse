from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.domain.build_jobs import BuildJobRecord, BuildJobTransitionError
from app.models import GraphBuildJob


def _record(row: GraphBuildJob) -> BuildJobRecord:
    return BuildJobRecord(
        row.id,
        row.job_key,
        row.position_id,
        row.status,
        dict(row.command),
        row.attempts,
        row.max_attempts,
        row.build_run_id,
        row.error_code,
        row.error_message,
        row.available_at,
    )


class SqlAlchemyBuildJobRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def enqueue(
        self, job_key: str, position_id: str, command: dict, max_attempts: int
    ) -> BuildJobRecord:
        active = self.session.scalar(
            select(GraphBuildJob.id)
            .where(
                GraphBuildJob.position_id == position_id,
                or_(
                    GraphBuildJob.status == "queued",
                    and_(
                        GraphBuildJob.status == "running",
                        GraphBuildJob.started_at
                        > datetime.now(timezone.utc) - timedelta(minutes=30),
                    ),
                ),
            )
            .limit(1)
        )
        if active is not None:
            raise BuildJobTransitionError(
                "当前岗位已有构建任务在排队或执行中，请等待完成后再发起"
            )
        row = GraphBuildJob(
            job_key=job_key,
            position_id=position_id,
            status="queued",
            command=command,
            max_attempts=max_attempts,
        )
        self.session.add(row)
        self.session.flush()
        return _record(row)

    def get(self, job_id: int) -> BuildJobRecord | None:
        row = self.session.get(GraphBuildJob, job_id)
        return _record(row) if row is not None else None

    def claim(self, worker_id: str, job_id: int | None = None) -> BuildJobRecord | None:
        now = datetime.now(timezone.utc)
        statement = select(GraphBuildJob).where(
            GraphBuildJob.status == "queued",
            GraphBuildJob.attempts < GraphBuildJob.max_attempts,
            GraphBuildJob.available_at <= now,
        )
        if job_id is not None:
            statement = statement.where(GraphBuildJob.id == job_id)
        if self.session.get_bind().dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        row = self.session.scalar(statement.order_by(GraphBuildJob.id).limit(1))
        if row is None:
            return None
        changed = self.session.execute(
            update(GraphBuildJob)
            .where(GraphBuildJob.id == row.id, GraphBuildJob.status == "queued")
            .values(
                status="running",
                attempts=GraphBuildJob.attempts + 1,
                worker_id=worker_id,
                started_at=now,
                finished_at=None,
            )
        )
        if changed.rowcount != 1:
            return None
        self.session.flush()
        return _record(self.session.get(GraphBuildJob, row.id))

    def succeed(self, job_id: int, build_run_id: int) -> BuildJobRecord:
        row = self.session.get(GraphBuildJob, job_id)
        if row is None or row.status != "running":
            raise BuildJobTransitionError("only a running build job may succeed")
        row.status = "succeeded"
        row.build_run_id = build_run_id
        row.error_code = None
        row.error_message = None
        row.finished_at = datetime.now(timezone.utc)
        self.session.flush()
        return _record(row)

    def fail(self, job_id: int, error_code: str, error_message: str) -> BuildJobRecord:
        row = self.session.get(GraphBuildJob, job_id)
        if row is None or row.status != "running":
            raise BuildJobTransitionError("only a running build job may fail")
        row.status = "failed"
        row.error_code = error_code[:80]
        row.error_message = error_message[:4000]
        row.finished_at = datetime.now(timezone.utc)
        self.session.flush()
        return _record(row)

    def retry(self, job_id: int) -> BuildJobRecord:
        row = self.session.get(GraphBuildJob, job_id)
        if row is None:
            raise BuildJobTransitionError("build job not found")
        if row.status != "failed":
            raise BuildJobTransitionError("only a failed build job may be retried")
        if row.attempts >= row.max_attempts:
            raise BuildJobTransitionError("build job retry limit has been reached")
        row.status = "queued"
        row.worker_id = None
        row.available_at = datetime.now(timezone.utc)
        self.session.flush()
        return _record(row)
