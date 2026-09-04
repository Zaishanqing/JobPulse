from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)

from app.core.database import Base
from app.models.user import utc_now

CANDIDATE_SUBMISSION_STATUSES = ("submitted", "revoked")


class CandidateSubmission(Base):
    __tablename__ = "candidate_submissions"
    __table_args__ = (
        UniqueConstraint(
            "enterprise_job_id", "resume_id", name="uq_candidate_submission_job_resume"
        ),
        CheckConstraint(
            f"status in {CANDIDATE_SUBMISSION_STATUSES}",
            name="ck_candidate_submissions_status_allowed",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    resume_id = Column(String(36), ForeignKey("resumes.id"), nullable=False, index=True)
    enterprise_job_id = Column(
        String(36), ForeignKey("enterprise_jobs.id"), nullable=False, index=True
    )
    enterprise_id = Column(
        String(36), ForeignKey("enterprises.id"), nullable=False, index=True
    )
    resume_owner_user_id = Column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    validated_cv_snapshot_id = Column(
        String(36),
        ForeignKey(
            "validated_cv_snapshots.id",
            ondelete="RESTRICT",
            name="fk_candidate_submissions_validated_snapshot",
        ),
        nullable=True,
        index=True,
    )
    status = Column(String(32), nullable=False, default="submitted")
    grant_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
