from __future__ import annotations

from urllib.parse import quote

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.contexts.evidence_rag import (
    EvidenceCitationTarget,
    EvidenceRagError,
    EvidenceRagHit,
)
from app.models.enterprise import Enterprise
from app.models.jd import JobDescription
from app.models.matching_service_reference import MatchingServiceReference
from app.models.resume import Resume
from app.models.source_cv import SourceCVVersion, ValidatedCVSnapshot
from app.models.source_jd import SourceJDVersion
from app.models.standard_position import StandardPosition


INTERNAL_READ_ROLES = frozenset({"admin", "developer"})


class SqlAlchemyEvidenceCitationTargetResolver:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def resolve(
        self, *, actor_id: str, actor_role: str, hit: EvidenceRagHit
    ) -> EvidenceCitationTarget:
        with self._session_factory() as session:
            if hit.source_object_type == "validated_cv_snapshot":
                return self._resume_target(session, actor_id, actor_role, hit)
            if hit.source_object_type in {"position_profile", "source_jd"}:
                return self._position_target(session, hit)
            if hit.source_object_type in {
                "matching_evaluation",
                "matching_evidence",
            }:
                return self._matching_target(session, actor_id, actor_role, hit)
            if hit.source_object_type in {"published_jd_fact", "jd_fact"}:
                return self._jd_target(session, actor_id, actor_role, hit)
        raise EvidenceRagError(
            "CITATION_SOURCE_UNSUPPORTED",
            f"Evidence source type {hit.source_object_type} has no citation target",
        )

    @staticmethod
    def _resume_target(
        session: Session, actor_id: str, actor_role: str, hit: EvidenceRagHit
    ) -> EvidenceCitationTarget:
        snapshot = session.get(ValidatedCVSnapshot, hit.source_object_id)
        if snapshot is None:
            raise EvidenceRagError(
                "CITATION_TARGET_NOT_FOUND", "Validated CV snapshot no longer exists"
            )
        source_version = session.get(SourceCVVersion, snapshot.source_cv_version_id)
        if source_version is None or hit.source_version not in {
            str(source_version.id),
            str(source_version.source_version),
        }:
            raise EvidenceRagError(
                "CITATION_VERSION_INVALID", "Validated CV snapshot version is invalid"
            )
        resume = session.scalar(
            select(Resume)
            .where(
                or_(
                    Resume.validated_cv_snapshot_id == snapshot.id,
                    Resume.source_cv_version_id == snapshot.source_cv_version_id,
                )
            )
            .order_by(Resume.updated_at.desc())
        )
        if resume is None:
            raise EvidenceRagError(
                "CITATION_TARGET_NOT_FOUND", "Snapshot is not linked to a Resume"
            )
        if actor_role not in INTERNAL_READ_ROLES and resume.user_id != actor_id:
            raise EvidenceRagError(
                "CITATION_PERMISSION_DENIED", "Resume Evidence is owned by another user"
            )
        resource_id = str(resume.id)
        return EvidenceCitationTarget(
            route=f"/profile/resumes?resumeId={quote(resource_id, safe='')}",
            resource_id=resource_id,
        )

    @staticmethod
    def _position_target(
        session: Session, hit: EvidenceRagHit
    ) -> EvidenceCitationTarget:
        resource_id = hit.business_object_id or hit.source_object_id
        if session.get(StandardPosition, resource_id) is None:
            raise EvidenceRagError(
                "CITATION_TARGET_NOT_FOUND", "Position Evidence target no longer exists"
            )
        return EvidenceCitationTarget(
            route=f"/positions/{quote(resource_id, safe='')}",
            resource_id=resource_id,
        )

    @staticmethod
    def _matching_target(
        session: Session, actor_id: str, actor_role: str, hit: EvidenceRagHit
    ) -> EvidenceCitationTarget:
        reference = session.scalar(
            select(MatchingServiceReference).where(
                or_(
                    MatchingServiceReference.evaluation_id == hit.source_object_id,
                    MatchingServiceReference.id == hit.source_object_id,
                )
            )
        )
        if reference is None or not reference.evaluation_id:
            raise EvidenceRagError(
                "CITATION_TARGET_NOT_FOUND", "Match evaluation no longer exists"
            )
        if reference.source_version not in {hit.source_version, "legacy-unspecified"}:
            raise EvidenceRagError(
                "CITATION_VERSION_INVALID", "Match evaluation version is invalid"
            )
        if actor_role not in INTERNAL_READ_ROLES and reference.user_id != actor_id:
            raise EvidenceRagError(
                "CITATION_PERMISSION_DENIED",
                "Match evaluation Evidence is owned by another user",
            )
        resource_id = str(reference.evaluation_id)
        return EvidenceCitationTarget(
            route=f"/matching/reports/{quote(resource_id, safe='')}",
            resource_id=resource_id,
        )

    @staticmethod
    def _jd_target(
        session: Session, actor_id: str, actor_role: str, hit: EvidenceRagHit
    ) -> EvidenceCitationTarget:
        jd = session.scalar(
            select(JobDescription).where(
                or_(
                    JobDescription.id == hit.source_object_id,
                    JobDescription.source_document_id == hit.source_document_id,
                    JobDescription.source_jd_id == hit.source_object_id,
                )
            )
        )
        if jd is None:
            raise EvidenceRagError(
                "CITATION_TARGET_NOT_FOUND", "JD Evidence target no longer exists"
            )
        if jd.source_jd_version_id:
            version = session.get(SourceJDVersion, jd.source_jd_version_id)
            if version is None or hit.source_version not in {
                str(version.id),
                str(version.source_version),
            }:
                raise EvidenceRagError(
                    "CITATION_VERSION_INVALID", "JD Evidence version is invalid"
                )
        if actor_role not in INTERNAL_READ_ROLES | {"reviewer"}:
            owner = (
                session.scalar(
                    select(Enterprise.owner_user_id).where(Enterprise.id == jd.enterprise_id)
                )
                if jd.enterprise_id
                else None
            )
            if actor_role != "enterprise_user" or owner != actor_id:
                raise EvidenceRagError(
                    "CITATION_PERMISSION_DENIED", "JD Evidence is outside the owned enterprise"
                )
        resource_id = str(jd.id)
        return EvidenceCitationTarget(
            route=f"/data/jds?jdId={quote(resource_id, safe='')}",
            resource_id=resource_id,
        )


__all__ = ["SqlAlchemyEvidenceCitationTargetResolver"]
