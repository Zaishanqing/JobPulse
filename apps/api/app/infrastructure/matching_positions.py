from sqlalchemy import or_
from sqlalchemy.orm import Session, sessionmaker

from app.contexts.matching_learning import MatchingPositionCandidate
from app.models.standard_position import StandardPosition


class SqlAlchemyMatchingPositionCatalog:
    """Matching-owned read adapter over the canonical position catalog."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _candidate(row: StandardPosition) -> MatchingPositionCandidate:
        return MatchingPositionCandidate(
            position_id=row.id,
            position_name=row.position_name,
            taxonomy_family_name=row.taxonomy_family_name,
            status=row.status,
            lifecycle_status=row.lifecycle_status,
            position_code=row.position_code,
            taxonomy_version=row.taxonomy_version,
        )

    def get(self, position_id: str) -> MatchingPositionCandidate | None:
        with self._session_factory() as session:
            row = (
                session.query(StandardPosition)
                .filter(
                    or_(
                        StandardPosition.id == position_id,
                        StandardPosition.position_code == position_id,
                    )
                )
                .one_or_none()
            )
            return self._candidate(row) if row is not None else None

    def list(self) -> list[MatchingPositionCandidate]:
        with self._session_factory() as session:
            rows = (
                session.query(StandardPosition)
                .order_by(StandardPosition.position_name)
                .all()
            )
            return [self._candidate(row) for row in rows]
