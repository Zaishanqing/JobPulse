from sqlalchemy.orm import Session, sessionmaker

from app.models.jd import JobDescription


class SqlAlchemyJDEmbeddingSource:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
    def get_text(self, object_id: str) -> str | None:
        with self._session_factory() as session:
            row = session.get(JobDescription, object_id)
            return f"{row.title}\n{row.raw_text}" if row is not None else None
