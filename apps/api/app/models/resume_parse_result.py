from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, JSON, String

from app.core.database import Base
from app.models.user import utc_now


class ResumeParseResult(Base):
    __tablename__ = "resume_parse_results"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    resume_id = Column(
        String(36),
        ForeignKey("resumes.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    education = Column(JSON, nullable=False, default=list)
    projects = Column(JSON, nullable=False, default=list)
    internships = Column(JSON, nullable=False, default=list)
    skills = Column(JSON, nullable=False, default=list)
    certificates = Column(JSON, nullable=False, default=list)
    competitions = Column(JSON, nullable=False, default=list)
    parse_confidence = Column(Float, nullable=False, default=0.88)
    need_review = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
