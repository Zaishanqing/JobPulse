from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domain.accounts import AccountActor


@dataclass(frozen=True)
class FileRecord:
    file_id: str
    owner_user_id: str
    filename: str
    content_type: str | None
    storage_key: str
    size: int
    purpose: str | None
    created_at: datetime | None


class FileRepository(Protocol):
    def add(self, *, owner_user_id: str, filename: str, content_type: str | None, storage_key: str, size: int, purpose: str | None) -> FileRecord: ...
    def get(self, file_id: str) -> FileRecord | None: ...
    def delete(self, file_id: str) -> None: ...


class BlobStoragePort(Protocol):
    def save(self, key: str, content: bytes) -> str: ...
    def read(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class FileUnitOfWork(Protocol):
    files: FileRepository
    def __enter__(self) -> "FileUnitOfWork": ...
    def __exit__(self, exc_type, exc, traceback) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class FileUploadWorkflowPort(Protocol):
    def upload(
        self, actor: AccountActor, *, filename: str,
        content_type: str | None, content: bytes, purpose: str | None,
    ) -> FileRecord: ...
