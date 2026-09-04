from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.tasks import SqlAlchemyTaskRepository
from app.integrations.base import IntegrationError
from app.integrations.contracts import OCRProvider
from app.models.ocr_result import OCRResult
from app.contexts.platform import OCRExtractionOutcome, OCRResultRecord
from app.contexts.tasks import TaskRecord


class IntegrationOCRExtractor:
    def __init__(self, provider: OCRProvider) -> None:
        self._provider = provider

    def extract(self, content: bytes, media_type: str) -> OCRExtractionOutcome:
        provider = self._provider.status().provider
        try:
            return OCRExtractionOutcome(provider, self._provider.extract_text(content, media_type), None, None)
        except IntegrationError as exc:
            return OCRExtractionOutcome(provider, None, exc.__class__.__name__, str(exc))


class SqlAlchemyOCRRepository:
    def __init__(self, session: Session) -> None:
        self._session = session
    def add(self, source_type, filename, outcome, created_by) -> OCRResultRecord:
        row = OCRResult(source_type=source_type, filename=filename, status="completed" if outcome.error_code is None else "failed", text=outcome.text, provider=outcome.provider, error_code=outcome.error_code, error_message=outcome.error_message, created_by=created_by)
        self._session.add(row)
        self._session.flush()
        return self._record(row)
    def get(self, result_id: str) -> OCRResultRecord | None:
        row = self._session.get(OCRResult, result_id)
        return self._record(row) if row is not None else None
    def update_text(self, result_id: str, text: str) -> OCRResultRecord:
        row = self._session.get(OCRResult, result_id)
        if row is None:
            raise LookupError(result_id)
        row.text, row.edited, row.status = text, True, "manually_edited"
        row.error_code = row.error_message = None
        self._session.flush()
        return self._record(row)
    @staticmethod
    def _record(row: OCRResult) -> OCRResultRecord:
        return OCRResultRecord(row.id, row.source_type, row.filename, row.status, row.text, row.provider, row.error_code, row.error_message, row.created_by, row.edited, row.created_at, row.updated_at)


class SqlAlchemyOCRUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
    def __enter__(self) -> "SqlAlchemyOCRUnitOfWork":
        self._session = self._session_factory()
        self.ocr = SqlAlchemyOCRRepository(self._session)
        self._tasks = SqlAlchemyTaskRepository(self._session)
        return self
    def add_task(self, task: TaskRecord) -> None:
        self._tasks.add(task)
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
