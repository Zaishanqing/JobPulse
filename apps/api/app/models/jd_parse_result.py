from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, JSON, String, Text

from app.core.database import Base
from app.models.user import utc_now


class JDParseResult(Base):
    __tablename__ = "jd_parse_results"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    jd_id = Column(
        String(36),
        ForeignKey("job_descriptions.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    position_title = Column(String(255), nullable=True)
    responsibilities = Column(JSON, nullable=False, default=list)
    required_skills = Column(JSON, nullable=False, default=list)
    bonus_skills = Column(JSON, nullable=False, default=list)
    education = Column(String(64), nullable=True)
    experience = Column(Text, nullable=True)
    industry = Column(String(128), nullable=True)
    tools = Column(JSON, nullable=False, default=list)
    business_scenarios = Column(JSON, nullable=False, default=list)
    parse_confidence = Column(Float, nullable=False, default=0.85)
    need_review = Column(Boolean, nullable=False, default=True)
    extraction_result = Column(JSON, nullable=True)
    normalized_result = Column(JSON, nullable=True)
    execution_metadata = Column(JSON, nullable=True)
    schema_version = Column(String(32), nullable=False, default="v2")
    normalization_schema_version = Column(String(32), nullable=False, default="v2")
    workflow_status = Column(String(32), nullable=False, default="draft")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
