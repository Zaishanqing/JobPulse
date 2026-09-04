from sqlalchemy.orm import Session, sessionmaker

from app.models.standard_position import StandardPosition


class SqlAlchemyPositionEmbeddingSource:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
    def get_text(self, object_id: str) -> str | None:
        with self._session_factory() as session:
            row = session.get(StandardPosition, object_id)
            if row is None:
                return None
            skills = [str(item.get("skill_name") or item.get("raw_skill") or "") for item in (row.required_skills or []) + (row.bonus_skills or [])]
            return " ".join([row.position_name, *[str(value) for value in row.core_responsibilities or []], *skills])


class SqlAlchemyRelationEmbeddingSource:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
    def get_text(self, object_id: str) -> str | None:
        with self._session_factory() as session:
            for row in session.query(StandardPosition).all():
                for skill in (row.required_skills or []) + (row.bonus_skills or []):
                    if skill.get("relation_id") == object_id:
                        return " ".join([row.position_name, str(skill.get("skill_name") or skill.get("raw_skill") or ""), str(skill.get("importance_level") or "")]).strip()
        return None
