from uuid import uuid4

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, String, Text

from app.core.database import Base
from app.models.user import utc_now


class Skill(Base):
    __tablename__ = "skills"
    __table_args__ = (
        CheckConstraint(
            "status in ('active', 'redirected', 'inactive')",
            name="ck_skills_status_allowed",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    catalog_code = Column(String(80), nullable=True, unique=True, index=True)
    skill_name = Column(String(128), nullable=False, unique=True, index=True)
    category = Column(String(128), nullable=True, index=True)
    description = Column(Text, nullable=True)
    parent_skill_id = Column(String(36), ForeignKey("skills.id"), nullable=True)
    status = Column(String(16), nullable=False, default="active", index=True)
    redirect_target_skill_id = Column(
        String(36),
        ForeignKey("skills.id"),
        nullable=True,
        index=True,
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
