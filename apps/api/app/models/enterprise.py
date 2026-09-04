from uuid import uuid4

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, String, Text

from app.core.database import Base
from app.models.user import utc_now


ENTERPRISE_STATUSES = ("active", "inactive")


class Enterprise(Base):
    __tablename__ = "enterprises"
    __table_args__ = (
        CheckConstraint(
            f"status in {ENTERPRISE_STATUSES}",
            name="ck_enterprises_status_allowed",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    enterprise_name = Column(String(255), nullable=False)
    industry = Column(String(128), nullable=True)
    scale = Column(String(64), nullable=True)
    location = Column(String(128), nullable=True)
    description = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
