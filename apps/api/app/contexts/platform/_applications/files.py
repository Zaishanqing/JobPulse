from dataclasses import dataclass
from typing import Callable
from uuid import uuid4

from app.domain.accounts import AccountActor
from app.domain.files import validate_upload
from app.contexts.platform._ports.files import BlobStoragePort, FileRecord, FileUnitOfWork
from app.domain.errors import PermissionDenied


class FileNotFound(LookupError):
    pass


@dataclass(frozen=True)
class ManageFiles:
    uow_factory: Callable[[], FileUnitOfWork]
    storage: BlobStoragePort
    maximum_size: Callable[[], int]

    def upload(self, actor: AccountActor, *, filename: str, content_type: str | None, content: bytes, purpose: str | None) -> FileRecord:
        safe_name, suffix = validate_upload(
            filename, content_type, content, self.maximum_size()
        )
        storage_key = self.storage.save(f"{uuid4()}{suffix}", content)
        try:
            with self.uow_factory() as uow:
                record = uow.files.add(
                    owner_user_id=actor.account_id, filename=safe_name,
                    content_type=content_type, storage_key=storage_key,
                    size=len(content), purpose=purpose,
                )
                uow.commit()
                return record
        except Exception:
            self.storage.delete(storage_key)
            raise

    def get(self, actor: AccountActor, file_id: str) -> FileRecord:
        with self.uow_factory() as uow:
            record = uow.files.get(file_id)
            if record is None:
                raise FileNotFound("File not found")
            self._authorize(actor, record)
            return record

    def read(self, actor: AccountActor, file_id: str) -> tuple[FileRecord, bytes]:
        record = self.get(actor, file_id)
        return record, self.storage.read(record.storage_key)

    def delete(self, actor: AccountActor, file_id: str) -> None:
        with self.uow_factory() as uow:
            record = uow.files.get(file_id)
            if record is None:
                raise FileNotFound("File not found")
            self._authorize(actor, record)
            self.storage.delete(record.storage_key)
            uow.files.delete(file_id)
            uow.commit()

    @staticmethod
    def _authorize(actor: AccountActor, record: FileRecord) -> None:
        if record.owner_user_id != actor.account_id and actor.role not in {"admin", "developer"}:
            raise PermissionDenied("No permission for this file")
