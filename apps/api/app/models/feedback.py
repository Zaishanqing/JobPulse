from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, JSON, String

from app.core.database import Base
from app.models.user import utc_now


class FeedbackRecord(Base):
    __tablename__ = "feedback_records"

    id = Column(String(64), primary_key=True, default=lambda: f"feedback_{uuid4()}")
    feedback_type = Column(String(64), nullable=False, index=True)
    created_by = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    payload = Column(JSON, nullable=False, default=dict)
    status = Column(String(32), nullable=False, default="pending_review", index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
