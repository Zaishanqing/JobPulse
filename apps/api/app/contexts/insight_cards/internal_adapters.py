from __future__ import annotations

from app.contexts.governance_feedback import (
    EvidenceRecord as GovernanceEvidenceRecord,
)
from app.contexts.insight_cards.review_chain import (
    ReviewDecisionProjection,
)
from app.infrastructure.governance import SqlAlchemyEvidenceRepository
from app.models.review_task import ReviewTask
from app.models.review_task_event import ReviewTaskEvent


class SqlAlchemyEvidenceReadAdapter:
    """Internal read-only Evidence access without governance role checks."""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    def get(
        self, evidence_id: str
    ) -> GovernanceEvidenceRecord | None:
        with self._session_factory() as session:
            return SqlAlchemyEvidenceRepository(session).get(evidence_id)

    def related(
        self, object_type: str, object_id: str
    ) -> list[GovernanceEvidenceRecord]:
        with self._session_factory() as session:
            return SqlAlchemyEvidenceRepository(session).related(
                object_type, object_id
            )


class SqlAlchemyReviewChainAdapter:
    """Internal scenario review chain without terminal-user governance role."""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    def create_scenario_review(
        self,
        object_type: str,
        object_id: str,
        priority: str,
        reason: str,
    ) -> str:
        with self._session_factory() as session:
            existing = self._existing(session, object_type, object_id)
            if existing is not None:
                return existing.id
            row = ReviewTask(
                object_type=object_type,
                object_id=object_id,
                priority=priority,
                reason=reason,
                status="pending",
            )
            session.add(row)
            session.flush()
            session.add(
                ReviewTaskEvent(
                    task_id=row.id,
                    actor_user_id="system:insight-card",
                    action="create",
                    before_status=None,
                    after_status="pending",
                    comment=reason,
                )
            )
            session.commit()
            return row.id

    def get_terminal_decision(
        self,
        object_type: str,
        object_id: str,
    ) -> ReviewDecisionProjection | None:
        with self._session_factory() as session:
            rows = (
                session.query(ReviewTask)
                .filter(
                    ReviewTask.object_type == object_type,
                    ReviewTask.object_id == object_id,
                    ReviewTask.status.in_(("approved", "rejected")),
                )
                .order_by(ReviewTask.updated_at.desc())
                .all()
            )
            if not rows:
                return None
            row = rows[0]
            return ReviewDecisionProjection(
                decision_id=row.id,
                decision=row.status,
                decided_at=row.updated_at,
                decided_by=row.reviewer_id,
                reason=row.review_comment,
            )

    @staticmethod
    def _existing(session, object_type: str, object_id: str):
        return (
            session.query(ReviewTask)
            .filter(
                ReviewTask.object_type == object_type,
                ReviewTask.object_id == object_id,
            )
            .first()
        )


__all__ = [
    "SqlAlchemyEvidenceReadAdapter",
    "SqlAlchemyReviewChainAdapter",
]
