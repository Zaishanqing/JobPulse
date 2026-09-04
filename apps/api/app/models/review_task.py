from uuid import uuid4

from sqlalchemy import CheckConstraint, Column, DateTime, Index, JSON, String, Text, text

from app.core.database import Base
from app.models.user import utc_now


REVIEW_TASK_STATUSES = ("pending", "claimed", "approved", "rejected", "modified")
REVIEW_TASK_PRIORITIES = ("low", "normal", "high", "urgent")


class ReviewTask(Base):
    __tablename__ = "review_tasks"
    __table_args__ = (
        CheckConstraint(
            f"status in {REVIEW_TASK_STATUSES}",
            name="ck_review_tasks_status_allowed",
        ),
        CheckConstraint(
            f"priority in {REVIEW_TASK_PRIORITIES}",
            name="ck_review_tasks_priority_allowed",
        ),
        Index(
            "uq_review_tasks_active_object",
            "object_type",
            "object_id",
            unique=True,
            sqlite_where=text("status IN ('pending', 'claimed')"),
            postgresql_where=text("status IN ('pending', 'claimed')"),
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    object_type = Column(String(64), nullable=False, index=True)
    object_id = Column(String(64), nullable=False, index=True)
    priority = Column(String(32), nullable=False, default="normal")
    reason = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="pending")
    reviewer_id = Column(String(36), nullable=True, index=True)
    review_comment = Column(Text, nullable=True)
    modified_payload = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
