from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, JSON, String, Text

from app.core.database import Base
from app.models.user import utc_now


class ReviewTaskEvent(Base):
    __tablename__ = "review_task_events"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    task_id = Column(String(36), ForeignKey("review_tasks.id"), nullable=False, index=True)
    actor_user_id = Column(String(36), nullable=False, index=True)
    action = Column(String(32), nullable=False)
    before_status = Column(String(32), nullable=True)
    after_status = Column(String(32), nullable=False)
    comment = Column(Text, nullable=True)
    payload_snapshot = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
