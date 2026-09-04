from sqlalchemy.orm import Session, sessionmaker

from app.models.evidence_source import EvidenceSource


class SqlAlchemyEvidenceEmbeddingSource:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
    def get_text(self, object_id: str) -> str | None:
        with self._session_factory() as session:
            row = session.get(EvidenceSource, object_id)
            return " ".join(filter(None, [row.title, row.source_name, row.raw_text])) if row is not None else None
