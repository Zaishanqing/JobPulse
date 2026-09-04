from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Float, ForeignKey, String

from app.core.database import Base
from app.models.user import utc_now


class EnterpriseJobSkillWeight(Base):
    __tablename__ = "enterprise_job_skill_weights"
    __table_args__ = (
        CheckConstraint("weight >= 0", name="ck_enterprise_job_weights_nonnegative"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    enterprise_job_id = Column(
        String(36),
        ForeignKey("enterprise_jobs.id"),
        nullable=False,
        index=True,
    )
    skill_id = Column(String(64), nullable=False, index=True)
    weight = Column(Float, nullable=False, default=0.0)
    is_required = Column(Boolean, nullable=False, default=False)
    is_bonus = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
