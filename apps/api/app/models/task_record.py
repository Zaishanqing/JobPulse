from uuid import uuid4

from sqlalchemy import CheckConstraint, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text

from app.core.database import Base
from app.models.user import utc_now


class TaskRecord(Base):
    __tablename__ = "task_records"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_task_records_status_allowed",
        ),
        CheckConstraint(
            "progress >= 0 and progress <= 1",
            name="ck_task_records_progress_range",
        ),
    )

    id = Column(String(80), primary_key=True, default=lambda: f"task_{uuid4()}")
    task_type = Column(String(80), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="pending", index=True)
    progress = Column(Float, nullable=False, default=0.0)
    input_payload = Column(JSON, nullable=False, default=dict)
    result_payload = Column(JSON, nullable=False, default=dict)
    result_reference = Column(String(255), nullable=True)
    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    created_by = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    attempt_count = Column(Integer, nullable=False, default=1)
    log_entries = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
