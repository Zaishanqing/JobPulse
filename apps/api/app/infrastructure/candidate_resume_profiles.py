from sqlalchemy.orm import Session, sessionmaker

from app.models.resume import Resume
from app.models.resume_skill import ResumeSkill
from app.contexts.talent_acquisition import CandidateResumeProfile


class SqlAlchemyCandidateResumeProfileAdapter:
    """Read-only resume adapter exposed through the candidate Port."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get(self, resume_id: str) -> CandidateResumeProfile | None:
        with self._session_factory() as session:
            resume = session.get(Resume, resume_id)
            if resume is None:
                return None
            return self._profile(session, resume)

    def list_for_owner(self, owner_id: str) -> list[CandidateResumeProfile]:
        with self._session_factory() as session:
            rows = (
                session.query(Resume)
                .filter(Resume.user_id == owner_id)
                .order_by(Resume.updated_at.desc(), Resume.id.desc())
                .all()
            )
            return [self._profile(session, resume) for resume in rows]

    @staticmethod
    def _profile(session: Session, resume: Resume) -> CandidateResumeProfile:
        skill_ids = frozenset(
            row[0]
            for row in session.query(ResumeSkill.skill_id)
            .filter(ResumeSkill.resume_id == resume.id)
            .all()
        )
        return CandidateResumeProfile(
            resume.id,
            resume.user_id,
            skill_ids,
            display_name=resume.display_name or resume.id,
            parse_status=resume.parse_status or "",
            validated_cv_snapshot_id=resume.validated_cv_snapshot_id,
        )
