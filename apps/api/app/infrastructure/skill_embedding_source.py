from sqlalchemy.orm import Session, sessionmaker

from app.models.skill import Skill


class SqlAlchemySkillEmbeddingSource:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
    def get_text(self, object_id: str) -> str | None:
        with self._session_factory() as session:
            row = session.get(Skill, object_id)
            return " ".join(filter(None, [row.skill_name, row.category, row.description])) if row is not None else None
