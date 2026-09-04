from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
)

from app.core.database import Base
from app.models.user import utc_now


class ReviewTaskOutcome(Base):
    __tablename__ = "review_task_outcomes"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    task_id = Column(
        String(36), ForeignKey("review_tasks.id"), nullable=False, index=True
    )
    terminal_event_id = Column(
        String(36), ForeignKey("review_task_events.id"), nullable=True
    )
    outcome_version = Column(String(32), nullable=False, default="v1")
    pre_state_fingerprint = Column(String(64), nullable=True)
    post_state_fingerprint = Column(String(64), nullable=True)
    blocking_released = Column(Boolean, nullable=True)
    correction_kind = Column(String(32), nullable=True)
    downstream_score_delta = Column(Float, nullable=True)
    downstream_effect_direction = Column(String(16), nullable=True)
    downstream_effect_magnitude = Column(Float, nullable=True)
    reuse_count_after_review = Column(Integer, nullable=True)
    observed_fields = Column(JSON, nullable=False, default=list)
    computed_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
