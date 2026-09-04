from uuid import uuid4

from sqlalchemy import Column, DateTime, Float, ForeignKey, String

from app.core.database import Base
from app.models.user import utc_now


class ResumeSkill(Base):
    __tablename__ = "resume_skills"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    resume_id = Column(String(36), ForeignKey("resumes.id"), nullable=False, index=True)
    skill_id = Column(String(64), nullable=False, index=True)
    raw_skill = Column(String(128), nullable=False)
    confidence = Column(Float, nullable=False, default=0.9)
    evidence = Column(String(255), nullable=True)
    proficiency = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
