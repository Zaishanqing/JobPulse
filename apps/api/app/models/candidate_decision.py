from uuid import uuid4

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, String, UniqueConstraint

from app.core.database import Base
from app.models.user import utc_now


class CandidateDecision(Base):
    __tablename__ = "candidate_decisions"
    __table_args__ = (
        UniqueConstraint("enterprise_job_id", "resume_id", name="uq_candidate_decision_job_resume"),
        CheckConstraint("decision in ('fit', 'unfit')", name="ck_candidate_decision_allowed"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    enterprise_job_id = Column(
        String(36), ForeignKey("enterprise_jobs.id"), nullable=False, index=True
    )
    resume_id = Column(String(36), ForeignKey("resumes.id"), nullable=False, index=True)
    decision = Column(String(16), nullable=False)
    decided_by = Column(String(36), ForeignKey("users.id"), nullable=False)
    evaluation_id = Column(String(200), nullable=True, index=True)
    task_id = Column(String(200), nullable=True)
    algorithm_version = Column(String(128), nullable=True)
    reason_code = Column(String(64), nullable=True)
    reason_text = Column(String(2000), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
