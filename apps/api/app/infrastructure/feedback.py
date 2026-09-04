from sqlalchemy.orm import Session, sessionmaker

from app.contexts.governance_feedback import FeedbackRecord, FeedbackTarget
from app.models.enterprise import Enterprise
from app.models.enterprise_job import EnterpriseJob
from app.models.feedback import FeedbackRecord as FeedbackRow
from app.models.jd import JobDescription
from app.models.matching_service_reference import MatchingServiceReference
from app.models.resume import Resume


class SqlAlchemyFeedbackRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, feedback_type, created_by, payload) -> FeedbackRecord:
        row = FeedbackRow(feedback_type=feedback_type, created_by=created_by, payload=dict(payload))
        self._session.add(row)
        self._session.flush()
        return self._record(row)

    def get(self, feedback_id: str) -> FeedbackRecord | None:
        row = self._session.get(FeedbackRow, feedback_id)
        return self._record(row) if row is not None else None

    def get_target(self, object_type: str, object_id: str) -> FeedbackTarget | None:
        if object_type == "resume":
            owner_id = self._session.query(Resume.user_id).filter(Resume.id == object_id).scalar()
            return FeedbackTarget(owner_id) if owner_id is not None else None
        if object_type in {"matching_evaluation", "learning_path"}:
            owner_id = (
                self._session.query(MatchingServiceReference.user_id)
                .filter(MatchingServiceReference.evaluation_id == object_id)
                .scalar()
            )
            return FeedbackTarget(owner_id) if owner_id is not None else None
        if object_type == "jd":
            row = self._session.get(JobDescription, object_id)
            if row is None:
                return None
            owner_id = None
            if row.enterprise_id is not None:
                owner_id = (
                    self._session.query(Enterprise.owner_user_id)
                    .filter(Enterprise.id == row.enterprise_id)
                    .scalar()
                )
            return FeedbackTarget(owner_id)
        if object_type == "enterprise_job":
            owner_id = (
                self._session.query(Enterprise.owner_user_id)
                .join(EnterpriseJob, EnterpriseJob.enterprise_id == Enterprise.id)
                .filter(EnterpriseJob.id == object_id)
                .scalar()
            )
            return FeedbackTarget(owner_id) if owner_id is not None else None
        return None

    def find_open_duplicate(
        self, created_by: str, object_type: str, object_id: str
    ) -> FeedbackRecord | None:
        rows = (
            self._session.query(FeedbackRow)
            .filter(
                FeedbackRow.created_by == created_by,
                FeedbackRow.status.in_(("pending_review", "reviewing")),
            )
            .all()
        )
        for row in rows:
            payload = row.payload or {}
            if (
                payload.get("object_type") == object_type
                and payload.get("object_id") == object_id
            ):
                return self._record(row)
        return None

    def list_page(
        self,
        *,
        owner_id: str | None,
        status: str | None,
        feedback_type: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[FeedbackRecord], int]:
        query = self._session.query(FeedbackRow)
        if owner_id is not None:
            query = query.filter(FeedbackRow.created_by == owner_id)
        if status is not None:
            query = query.filter(FeedbackRow.status == status)
        if feedback_type is not None:
            query = query.filter(FeedbackRow.feedback_type == feedback_type)
        total = query.count()
        rows = (
            query.order_by(FeedbackRow.created_at.desc(), FeedbackRow.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [self._record(row) for row in rows], total

    def update(self, feedback_id, payload, status) -> FeedbackRecord:
        row = self._session.get(FeedbackRow, feedback_id)
        if row is None:
            raise LookupError(feedback_id)
        if payload is not None:
            row.payload = dict(payload)
        if status is not None:
            row.status = status
        self._session.flush()
        return self._record(row)

    @staticmethod
    def _record(row: FeedbackRow) -> FeedbackRecord:
        return FeedbackRecord(row.id, row.feedback_type, row.created_by, row.payload or {}, row.status, row.created_at, row.updated_at)


class SqlAlchemyFeedbackUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
    def __enter__(self) -> "SqlAlchemyFeedbackUnitOfWork":
        self._session = self._session_factory()
        self.feedback = SqlAlchemyFeedbackRepository(self._session)
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
