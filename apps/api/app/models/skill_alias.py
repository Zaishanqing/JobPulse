from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, String, UniqueConstraint

from app.core.database import Base
from app.models.user import utc_now


class SkillAlias(Base):
    __tablename__ = "skill_aliases"
    __table_args__ = (
        UniqueConstraint("skill_id", "alias", name="uq_skill_aliases_skill_alias"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    skill_id = Column(String(36), ForeignKey("skills.id"), nullable=False, index=True)
    alias = Column(String(128), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
