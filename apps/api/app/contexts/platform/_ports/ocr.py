from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.contexts.tasks import TaskRecord


@dataclass(frozen=True)
class OCRExtractionOutcome:
    provider: str
    text: str | None
    error_code: str | None
    error_message: str | None


@dataclass(frozen=True)
class OCRResultRecord:
    result_id: str
    source_type: str
    filename: str | None
    status: str
    text: str | None
    provider: str
    error_code: str | None
    error_message: str | None
    created_by: str
    edited: bool
    created_at: datetime | None
    updated_at: datetime | None


class OCRExtractionPort(Protocol):
    def extract(self, content: bytes, media_type: str) -> OCRExtractionOutcome: ...


class OCRRepository(Protocol):
    def add(self, source_type: str, filename: str | None, outcome: OCRExtractionOutcome, created_by: str) -> OCRResultRecord: ...
    def get(self, result_id: str) -> OCRResultRecord | None: ...
    def update_text(self, result_id: str, text: str) -> OCRResultRecord: ...


class OCRUnitOfWork(Protocol):
    ocr: OCRRepository
    def add_task(self, task: TaskRecord) -> None: ...
    def __enter__(self) -> "OCRUnitOfWork": ...
    def __exit__(self, exc_type, exc, traceback) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
