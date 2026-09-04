from sqlalchemy.orm import sessionmaker, Session

from app.models.resume import Resume
from app.models.resume_parse_result import ResumeParseResult
from app.models.resume_skill import ResumeSkill
from app.models.source_cv import ValidatedCVSnapshot
from app.contexts.matching_learning import ResumeProfile
from app.domain.matching import ResumeProject, ResumeSkillIdentity


class SqlAlchemyResumeProfileAdapter:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
    def get(self, resume_id: str) -> ResumeProfile | None:
        with self._session_factory() as session:
            resume = session.get(Resume, resume_id)
            if resume is None:
                return None
            snapshot = (
                session.get(ValidatedCVSnapshot, resume.validated_cv_snapshot_id)
                if resume.validated_cv_snapshot_id
                else None
            )
            rows = session.query(ResumeSkill).filter(ResumeSkill.resume_id == resume_id).all()
            skills = tuple(
                ResumeSkillIdentity(row.skill_id, row.confidence, row.proficiency)
                for row in rows
            )
            skill_ids = {row.raw_skill.casefold(): row.skill_id for row in rows}
            parse_result = (
                session.query(ResumeParseResult)
                .filter(ResumeParseResult.resume_id == resume_id)
                .one_or_none()
            )
            projects = resume_projects(
                parse_result.projects if parse_result else (),
                skill_ids,
            )
            return ResumeProfile(
                resume.id,
                resume.user_id,
                resume.validated_cv_snapshot_id,
                skills,
                projects,
                (
                    f"snapshot={snapshot.id}:{snapshot.snapshot_revision}"
                    if snapshot is not None
                    else str(resume.source_cv_version_id or "")
                ),
            )

    def list_for_owner(self, owner_id: str) -> list[ResumeProfile]:
        with self._session_factory() as session:
            ids = [
                value
                for (value,) in session.query(Resume.id)
                .filter(
                    Resume.user_id == owner_id,
                    Resume.validated_cv_snapshot_id.is_not(None),
                )
                .order_by(Resume.updated_at.desc())
                .all()
            ]
        return [profile for resume_id in ids if (profile := self.get(resume_id)) is not None]


def resume_projects(
    items: object,
    skill_ids: dict[str, str],
) -> tuple[ResumeProject, ...]:
    """Map persisted parse-result JSON to deterministic matching inputs."""
    if not isinstance(items, (list, tuple)):
        return ()
    return tuple(
        project
        for item in items
        if isinstance(item, dict)
        and (project := _project(item, skill_ids)) is not None
    )


def _project(
    item: dict[str, object], skill_ids: dict[str, str]
) -> ResumeProject | None:
    name = item.get("project_name") or item.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    raw_skills = item.get("tech_stack") or item.get("skills") or ()
    project_skills: list[str] = []
    if isinstance(raw_skills, list):
        for value in raw_skills:
            if isinstance(value, dict):
                candidate = (
                    value.get("skill_id")
                    or value.get("normalized_skill_id")
                    or value.get("name")
                )
            else:
                candidate = value
            if isinstance(candidate, str) and candidate.strip():
                project_skills.append(
                    skill_ids.get(candidate.casefold(), candidate)
                )
    description = item.get("description")
    if isinstance(description, dict):
        description = description.get("value")
    evidence = item.get("evidence")
    if isinstance(evidence, dict):
        evidence = evidence.get("quote")
    return ResumeProject(
        name.strip(),
        str(description) if description else None,
        tuple(project_skills),
        str(evidence) if evidence else None,
    )
