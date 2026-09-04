from sqlalchemy.orm import Session, sessionmaker

from app.models.task_record import TaskRecord as TaskRow
from app.domain.values import thaw
from app.contexts.tasks import TaskLog, TaskPayload, TaskRecord


class SqlAlchemyTaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session
    def add(self, record: TaskRecord) -> None:
        self._session.add(TaskRow(
            id=record.task_id, task_type=record.task_type, status=record.status,
            progress=record.progress, input_payload=thaw(record.input_payload.values),
            result_payload=thaw(record.result_payload.values), result_reference=record.result_reference,
            error_code=record.error_code, error_message=record.error_message,
            created_by=record.created_by, attempt_count=record.attempt_count,
            log_entries=[{"status": item.status, "at": item.at, "message": item.message} for item in record.logs], created_at=record.created_at,
            updated_at=record.updated_at, started_at=record.started_at, finished_at=record.finished_at,
        ))
    def get(self, task_id: str) -> TaskRecord | None:
        row = self._session.get(TaskRow, task_id)
        return self._record(row) if row is not None else None
    def list(self) -> list[TaskRecord]:
        return [self._record(row) for row in self._session.query(TaskRow).order_by(TaskRow.created_at.desc()).all()]
    def save(self, record: TaskRecord) -> None:
        row = self._session.get(TaskRow, record.task_id)
        if row is None:
            raise LookupError(record.task_id)
        for name, value in {
            "status": record.status, "progress": record.progress,
            "result_payload": thaw(record.result_payload.values), "result_reference": record.result_reference,
            "error_code": record.error_code, "error_message": record.error_message,
            "attempt_count": record.attempt_count, "log_entries": [{"status": item.status, "at": item.at, "message": item.message} for item in record.logs],
            "updated_at": record.updated_at, "started_at": record.started_at,
            "finished_at": record.finished_at,
        }.items():
            setattr(row, name, value)
    @staticmethod
    def _record(row: TaskRow) -> TaskRecord:
        return TaskRecord(
            row.id, row.task_type, row.status, row.progress, TaskPayload.from_mapping(row.input_payload),
            TaskPayload.from_mapping(row.result_payload), row.result_reference, row.error_code,
            row.error_message, row.created_by, row.attempt_count,
            tuple(TaskLog(str(item.get("status", "")), str(item.get("at", "")), item.get("message")) for item in row.log_entries or ()), row.created_at, row.updated_at,
            row.started_at, row.finished_at,
        )


class SqlAlchemyTaskUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
    def __enter__(self) -> "SqlAlchemyTaskUnitOfWork":
        self._session = self._session_factory()
        self.tasks = SqlAlchemyTaskRepository(self._session)
        return self
    def __exit__(self, exc_type, exc, traceback) -> None:
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
