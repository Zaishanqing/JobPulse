from sqlalchemy.orm import Session, sessionmaker

from app.domain.candidates import WeightedSkill
from app.models.enterprise import Enterprise
from app.models.enterprise_job import EnterpriseJob
from app.models.enterprise_job_weight import EnterpriseJobSkillWeight
from app.contexts.talent_acquisition import CandidateJobProfile


class SqlAlchemyCandidateJobProfileAdapter:
    """Read-only recruitment adapter exposed through the candidate Port."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get(self, job_id: str) -> CandidateJobProfile | None:
        with self._session_factory() as session:
            job = session.get(EnterpriseJob, job_id)
            if job is None:
                return None
            enterprise = session.get(Enterprise, job.enterprise_id)
            if enterprise is None:
                return None
            rows = (
                session.query(EnterpriseJobSkillWeight)
                .filter(EnterpriseJobSkillWeight.enterprise_job_id == job_id)
                .order_by(EnterpriseJobSkillWeight.created_at.asc())
                .all()
            )
            weights = tuple(
                WeightedSkill(
                    row.skill_id,
                    row.weight,
                    "bonus" if row.is_bonus else "core" if row.is_required else "normal",
                )
                for row in rows
            )
            return CandidateJobProfile(
                job.id, job.enterprise_id, enterprise.owner_user_id, weights, job.status
            )
