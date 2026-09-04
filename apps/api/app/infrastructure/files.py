from sqlalchemy.orm import Session, sessionmaker

from app.models.file_asset import FileAsset
from app.contexts.platform import FileRecord


class SqlAlchemyFileRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, **values) -> FileRecord:
        storage_key = values.pop("storage_key")
        row = FileAsset(path=storage_key, **values)
        self._session.add(row)
        self._session.flush()
        return self._record(row)

    def get(self, file_id: str) -> FileRecord | None:
        row = self._session.get(FileAsset, file_id)
        return self._record(row) if row is not None else None

    def delete(self, file_id: str) -> None:
        row = self._session.get(FileAsset, file_id)
        if row is None:
            raise LookupError(file_id)
        self._session.delete(row)

    @staticmethod
    def _record(row: FileAsset) -> FileRecord:
        return FileRecord(row.id, row.owner_user_id, row.filename, row.content_type, row.path, row.size, row.purpose, row.created_at)


class SqlAlchemyFileUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
    def __enter__(self) -> "SqlAlchemyFileUnitOfWork":
        self._session = self._session_factory()
        self.files = SqlAlchemyFileRepository(self._session)
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
