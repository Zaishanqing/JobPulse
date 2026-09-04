from sqlalchemy.orm import Session, sessionmaker

from app.models.candidate_decision import CandidateDecision
from app.models.candidate_submission import CandidateSubmission
from app.models.resume import Resume
from app.models.resume_skill import ResumeSkill
from app.contexts.talent_acquisition import (
    CandidateDecisionRecord,
    CandidateJobProfile,
    CandidateResumeProfile,
    CandidateSubmissionRecord,
)
from app.infrastructure.tasks import SqlAlchemyTaskRepository
from app.contexts.tasks import TaskRecord
from app.infrastructure.outbox import SqlAlchemyOutboxRepository


class SqlAlchemyCandidateRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_submission(
        self, job_id: str, resume_id: str
    ) -> CandidateSubmissionRecord | None:
        row = self._submission_row(job_id, resume_id)
        return self._submission(row) if row is not None else None

    def get_submission_by_id(
        self, job_id: str, submission_id: str
    ) -> CandidateSubmissionRecord | None:
        row = (
            self._session.query(CandidateSubmission)
            .filter(
                CandidateSubmission.id == submission_id,
                CandidateSubmission.enterprise_job_id == job_id,
            )
            .first()
        )
        return self._submission(row) if row is not None else None

    def list_submissions(self, job_id: str) -> list[CandidateSubmissionRecord]:
        rows = (
            self._session.query(CandidateSubmission)
            .filter(CandidateSubmission.enterprise_job_id == job_id)
            .order_by(CandidateSubmission.created_at.desc(), CandidateSubmission.id.desc())
            .all()
        )
        return [self._submission(row) for row in rows]

    def save_submission(
        self, job: CandidateJobProfile, resume: CandidateResumeProfile, status: str
    ) -> CandidateSubmissionRecord:
        row = self._submission_row(job.job_id, resume.resume_id)
        if row is None:
            row = CandidateSubmission(
                resume_id=resume.resume_id,
                enterprise_job_id=job.job_id,
                enterprise_id=job.enterprise_id,
                resume_owner_user_id=resume.owner_id,
                validated_cv_snapshot_id=resume.validated_cv_snapshot_id,
                status=status,
            )
            self._session.add(row)
        else:
            if (
                row.status != status
                or row.validated_cv_snapshot_id != resume.validated_cv_snapshot_id
            ):
                row.grant_version += 1
            row.enterprise_id = job.enterprise_id
            row.resume_owner_user_id = resume.owner_id
            row.validated_cv_snapshot_id = resume.validated_cv_snapshot_id
            row.status = status
        self._session.flush()
        return self._submission(row)

    def is_submitted(
        self, job: CandidateJobProfile, resume: CandidateResumeProfile
    ) -> bool:
        row = (
            self._session.query(CandidateSubmission.id)
            .filter(
                CandidateSubmission.enterprise_job_id == job.job_id,
                CandidateSubmission.enterprise_id == job.enterprise_id,
                CandidateSubmission.resume_id == resume.resume_id,
                CandidateSubmission.resume_owner_user_id == resume.owner_id,
                CandidateSubmission.status == "submitted",
            )
            .first()
        )
        return row is not None

    def list_decisions(self, job_id: str) -> list[CandidateDecisionRecord]:
        rows = (
            self._session.query(CandidateDecision)
            .filter(CandidateDecision.enterprise_job_id == job_id)
            .order_by(CandidateDecision.updated_at.desc(), CandidateDecision.id.desc())
            .all()
        )
        return [self._decision(row) for row in rows]

    def save_decision(
        self, job_id: str, resume_id: str, decision: str, actor_id: str,
        evaluation_id: str | None = None,
        task_id: str | None = None,
        algorithm_version: str | None = None,
        reason_code: str | None = None,
        reason_text: str | None = None,
    ) -> CandidateDecisionRecord:
        row = (
            self._session.query(CandidateDecision)
            .filter(
                CandidateDecision.enterprise_job_id == job_id,
                CandidateDecision.resume_id == resume_id,
            )
            .first()
        )
        if row is None:
            row = CandidateDecision(
                enterprise_job_id=job_id,
                resume_id=resume_id,
                decision=decision,
                decided_by=actor_id,
                evaluation_id=evaluation_id,
                task_id=task_id,
                algorithm_version=algorithm_version,
                reason_code=reason_code,
                reason_text=reason_text,
            )
            self._session.add(row)
        else:
            row.decision = decision
            row.decided_by = actor_id
            row.evaluation_id = evaluation_id
            row.task_id = task_id
            row.algorithm_version = algorithm_version
            row.reason_code = reason_code
            row.reason_text = reason_text
        self._session.flush()
        return self._decision(row)

    def _submission_row(
        self, job_id: str, resume_id: str
    ) -> CandidateSubmission | None:
        return (
            self._session.query(CandidateSubmission)
            .filter(
                CandidateSubmission.enterprise_job_id == job_id,
                CandidateSubmission.resume_id == resume_id,
            )
            .first()
        )

    def _submission(self, row: CandidateSubmission) -> CandidateSubmissionRecord:
        resume = self._session.get(Resume, row.resume_id)
        skill_count = (
            self._session.query(ResumeSkill.id)
            .filter(ResumeSkill.resume_id == row.resume_id)
            .count()
        )
        return CandidateSubmissionRecord(
            row.id,
            row.resume_id,
            row.enterprise_job_id,
            row.enterprise_id,
            row.resume_owner_user_id,
            row.status,
            row.created_at,
            row.updated_at,
            row.grant_version,
            display_name=resume.display_name or resume.id if resume else row.resume_id,
            parse_status=resume.parse_status if resume else "",
            validated_cv_snapshot_id=row.validated_cv_snapshot_id,
            skill_count=skill_count,
        )

    @staticmethod
    def _decision(row: CandidateDecision) -> CandidateDecisionRecord:
        return CandidateDecisionRecord(
            decision_id=row.id,
            job_id=row.enterprise_job_id,
            resume_id=row.resume_id,
            decision=row.decision,
            decided_by=row.decided_by,
            evaluation_id=row.evaluation_id,
            task_id=row.task_id,
            algorithm_version=row.algorithm_version,
            reason_code=row.reason_code,
            reason_text=row.reason_text,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class SqlAlchemyCandidateUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    def __enter__(self) -> "SqlAlchemyCandidateUnitOfWork":
        self._session = self._session_factory()
        self.candidates = SqlAlchemyCandidateRepository(self._session)
        self._tasks = SqlAlchemyTaskRepository(self._session)
        return self

    def add_task(self, record: TaskRecord) -> None:
        self._tasks.add(record)

    def add_outbox(self, draft) -> None:
        if self._session is None:
            raise RuntimeError("unit of work has not been entered")
        SqlAlchemyOutboxRepository(self._session).add(draft)

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
