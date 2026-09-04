from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker
from app.infrastructure.outbox import SqlAlchemyOutboxRepository

from app.models.enterprise import Enterprise
from app.models.candidate_decision import CandidateDecision
from app.models.candidate_submission import CandidateSubmission
from app.models.enterprise_job import EnterpriseJob
from app.models.enterprise_job_weight import EnterpriseJobSkillWeight
from app.contexts.talent_acquisition import (
    JobRecord,
    PublishedJobRecord,
    SkillWeightInput,
    SkillWeightRecord,
)


class SqlAlchemyJobRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def enterprise_owner(self, enterprise_id: str) -> str | None:
        row = self._session.get(Enterprise, enterprise_id)
        return row.owner_user_id if row is not None else None

    def get(self, job_id: str) -> JobRecord | None:
        row = self._session.get(EnterpriseJob, job_id)
        return self._job(row) if row is not None else None

    def list_all(self) -> list[JobRecord]:
        rows = self._session.query(EnterpriseJob).order_by(EnterpriseJob.created_at.desc()).all()
        return [self._job(row) for row in rows]

    def list_for_owner(self, owner_id: str) -> list[JobRecord]:
        rows = (
            self._session.query(EnterpriseJob)
            .join(Enterprise, Enterprise.id == EnterpriseJob.enterprise_id)
            .filter(Enterprise.owner_user_id == owner_id)
            .order_by(EnterpriseJob.created_at.desc())
            .all()
        )
        return [self._job(row) for row in rows]

    def list_published(self) -> list[PublishedJobRecord]:
        rows = (
            self._session.query(EnterpriseJob, Enterprise.enterprise_name)
            .join(Enterprise, Enterprise.id == EnterpriseJob.enterprise_id)
            .filter(
                EnterpriseJob.status == "published",
                Enterprise.status == "active",
            )
            .order_by(EnterpriseJob.updated_at.desc())
            .all()
        )
        return [self._published_job(job, enterprise_name) for job, enterprise_name in rows]

    def get_published(self, job_id: str) -> PublishedJobRecord | None:
        row = (
            self._session.query(EnterpriseJob, Enterprise.enterprise_name)
            .join(Enterprise, Enterprise.id == EnterpriseJob.enterprise_id)
            .filter(
                EnterpriseJob.id == job_id,
                EnterpriseJob.status == "published",
                Enterprise.status == "active",
            )
            .first()
        )
        return self._published_job(*row) if row is not None else None

    def add(self, values: dict[str, object]) -> JobRecord:
        row = EnterpriseJob(**values)
        self._session.add(row)
        self._session.flush()
        return self._job(row)

    def update(self, job_id: str, changes: dict[str, object]) -> JobRecord:
        row = self._required(job_id)
        for key, value in changes.items():
            setattr(row, key, value)
        self._session.flush()
        return self._job(row)

    def delete(self, job_id: str) -> None:
        job = self._required(job_id)
        self._session.query(CandidateDecision).filter(
            CandidateDecision.enterprise_job_id == job_id
        ).delete(synchronize_session=False)
        self._session.query(CandidateSubmission).filter(
            CandidateSubmission.enterprise_job_id == job_id
        ).delete(synchronize_session=False)
        self._session.query(EnterpriseJobSkillWeight).filter(
            EnterpriseJobSkillWeight.enterprise_job_id == job_id
        ).delete(synchronize_session=False)
        self._session.delete(job)

    def list_weights(self, job_id: str) -> list[SkillWeightRecord]:
        rows = (
            self._session.query(EnterpriseJobSkillWeight)
            .filter(EnterpriseJobSkillWeight.enterprise_job_id == job_id)
            .order_by(EnterpriseJobSkillWeight.created_at.asc())
            .all()
        )
        return [self._weight(row) for row in rows]

    def replace_weights(
        self, job_id: str, weights: list[SkillWeightInput]
    ) -> list[SkillWeightRecord]:
        self._session.query(EnterpriseJobSkillWeight).filter(
            EnterpriseJobSkillWeight.enterprise_job_id == job_id
        ).delete()
        rows = [
            EnterpriseJobSkillWeight(
                enterprise_job_id=job_id,
                skill_id=item.skill_id,
                weight=item.weight,
                is_required=item.is_required,
                is_bonus=item.is_bonus,
            )
            for item in weights
        ]
        self._session.add_all(rows)
        self._session.flush()
        return [self._weight(row) for row in rows]

    def clear_weights(self, job_id: str) -> int:
        return self._session.query(EnterpriseJobSkillWeight).filter(
            EnterpriseJobSkillWeight.enterprise_job_id == job_id
        ).delete()

    def _required(self, job_id: str) -> EnterpriseJob:
        row = self._session.get(EnterpriseJob, job_id)
        if row is None:
            raise LookupError(job_id)
        return row

    @staticmethod
    def _job(row: EnterpriseJob) -> JobRecord:
        return JobRecord(
            row.id, row.enterprise_id, row.title, row.standard_position_id,
            row.jd_text, row.requirement_graph, row.headcount, row.location, row.employment_type,
            row.salary_min, row.salary_max, row.salary_unit, row.status, row.created_at, row.updated_at,
        )

    @staticmethod
    def _published_job(row: EnterpriseJob, enterprise_name: str) -> PublishedJobRecord:
        return PublishedJobRecord(
            row.id,
            enterprise_name,
            row.title,
            row.jd_text,
            row.headcount,
            row.location,
            row.employment_type,
            row.salary_min,
            row.salary_max,
            row.salary_unit,
            row.status,
        )

    @staticmethod
    def _weight(row: EnterpriseJobSkillWeight) -> SkillWeightRecord:
        return SkillWeightRecord(
            row.id, row.enterprise_job_id, row.skill_id, row.weight,
            row.is_required, row.is_bonus,
        )


class SqlAlchemyRecruitmentUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    def __enter__(self) -> "SqlAlchemyRecruitmentUnitOfWork":
        self._session = self._session_factory()
        self.jobs = SqlAlchemyJobRepository(self._session)
        return self

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
