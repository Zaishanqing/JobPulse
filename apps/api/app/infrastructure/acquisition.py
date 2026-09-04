from __future__ import annotations

from datetime import datetime, timedelta
from types import TracebackType

from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from app.contexts.acquisition.domain import RUNNING_STATUSES
from app.contexts.acquisition.domain import AcquisitionJobRecord
from app.models.acquisition_job import AcquisitionJob as AcquisitionJobRow


class SqlAlchemyAcquisitionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: AcquisitionJobRecord) -> None:
        self._session.add(self._row(record))

    def get(self, job_id: str) -> AcquisitionJobRecord | None:
        row = self._session.get(AcquisitionJobRow, job_id)
        return self._record(row) if row is not None else None

    def claim_pending(
        self,
        job_id: str,
        now: datetime,
    ) -> AcquisitionJobRecord | None:
        claimed = self._session.execute(
            update(AcquisitionJobRow)
            .where(
                AcquisitionJobRow.id == job_id,
                AcquisitionJobRow.status == "pending",
            )
            .values(
                status="crawling",
                started_at=now,
                progress=0.1,
                updated_at=now,
            )
        )
        if claimed.rowcount != 1:
            return None
        row = self._session.get(AcquisitionJobRow, job_id)
        return self._record(row) if row is not None else None

    def list(
        self,
        *,
        status: str | None = None,
        source: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[AcquisitionJobRecord], int]:
        query = self._session.query(AcquisitionJobRow)
        if status is not None:
            query = query.filter(AcquisitionJobRow.status == status)
        if source is not None:
            query = query.filter(AcquisitionJobRow.source == source)
        total = query.count()
        rows = (
            query.order_by(AcquisitionJobRow.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [self._record(row) for row in rows], total

    def save(self, record: AcquisitionJobRecord) -> None:
        row = self._session.get(AcquisitionJobRow, record.id)
        if row is None:
            raise LookupError(record.id)
        for name, value in {
            "requested_by": record.requested_by,
            "source": record.source,
            "keyword": record.keyword,
            "city": record.city,
            "pages": record.pages,
            "status": record.status,
            "progress": record.progress,
            "crawler_task_id": record.crawler_task_id,
            "bundle_id": record.bundle_id,
            "bundle_file_name": record.bundle_file_name,
            "bundle_hash": record.bundle_hash,
            "discovered_count": record.discovered_count,
            "exported_count": record.exported_count,
            "imported_count": record.imported_count,
            "no_op_count": record.no_op_count,
            "failed_count": record.failed_count,
            "import_batch_id": record.import_batch_id,
            "error_code": record.error_code,
            "error_message": record.error_message,
            "retry_of_id": record.retry_of_id,
            "attempt": record.attempt,
            "updated_at": record.updated_at,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
        }.items():
            setattr(row, name, value)

    def recover_stale(self, now: datetime, stale_after_seconds: float) -> int:
        threshold = now - timedelta(seconds=stale_after_seconds)
        rows = (
            self._session.query(AcquisitionJobRow)
            .filter(
                AcquisitionJobRow.status.in_(tuple(RUNNING_STATUSES)),
                AcquisitionJobRow.updated_at < threshold,
            )
            .all()
        )
        for row in rows:
            row.status = "cancelled"
            row.error_code = "ACQUISITION_INTERRUPTED"
            row.error_message = "Acquisition job was interrupted by process restart"
            row.finished_at = now
            row.updated_at = now
        return len(rows)

    @staticmethod
    def _row(record: AcquisitionJobRecord) -> AcquisitionJobRow:
        return AcquisitionJobRow(
            id=record.id,
            requested_by=record.requested_by,
            source=record.source,
            keyword=record.keyword,
            city=record.city,
            pages=record.pages,
            status=record.status,
            progress=record.progress,
            crawler_task_id=record.crawler_task_id,
            bundle_id=record.bundle_id,
            bundle_file_name=record.bundle_file_name,
            bundle_hash=record.bundle_hash,
            discovered_count=record.discovered_count,
            exported_count=record.exported_count,
            imported_count=record.imported_count,
            no_op_count=record.no_op_count,
            failed_count=record.failed_count,
            import_batch_id=record.import_batch_id,
            error_code=record.error_code,
            error_message=record.error_message,
            retry_of_id=record.retry_of_id,
            attempt=record.attempt,
            created_at=record.created_at,
            updated_at=record.updated_at,
            started_at=record.started_at,
            finished_at=record.finished_at,
        )

    @staticmethod
    def _record(row: AcquisitionJobRow) -> AcquisitionJobRecord:
        return AcquisitionJobRecord(
            id=row.id,
            requested_by=row.requested_by,
            source=row.source,
            keyword=row.keyword,
            city=row.city,
            pages=row.pages,
            status=row.status,
            progress=row.progress,
            crawler_task_id=row.crawler_task_id,
            bundle_id=row.bundle_id,
            bundle_file_name=row.bundle_file_name,
            bundle_hash=row.bundle_hash,
            discovered_count=row.discovered_count,
            exported_count=row.exported_count,
            imported_count=row.imported_count,
            no_op_count=row.no_op_count,
            failed_count=row.failed_count,
            import_batch_id=row.import_batch_id,
            error_code=row.error_code,
            error_message=row.error_message,
            retry_of_id=row.retry_of_id,
            attempt=row.attempt,
            created_at=row.created_at,
            updated_at=row.updated_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )


class SqlAlchemyAcquisitionUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    def __enter__(self) -> "SqlAlchemyAcquisitionUnitOfWork":
        self._session = self._session_factory()
        self.acquisition = SqlAlchemyAcquisitionRepository(self._session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._session is None:
            return
        if exc_type is not None:
            self.rollback()
        self._session.close()

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("unit of work has not been entered")
        self._session.commit()

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()
