from uuid import uuid4

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, JSON, String

from app.core.database import Base
from app.models.user import utc_now


class LearningPathRecord(Base):
    """Persisted snapshot returned by the main-system learning-path API."""

    __tablename__ = "learning_path_records"
    __table_args__ = (
        Index("ix_learning_path_owner_created", "user_id", "created_at"),
        Index("ix_learning_path_evaluation_created", "evaluation_id", "created_at"),
    )

    path_id = Column(
        String(80), primary_key=True, default=lambda: f"learning-path:{uuid4()}"
    )
    evaluation_id = Column(
        String(200),
        ForeignKey("matching_service_references.evaluation_id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False)
    target_position_id = Column(String(64), nullable=True, index=True)
    time_budget_hours = Column(Float, nullable=True)
    gap_analysis = Column(JSON, nullable=False)
    status = Column(String(32), nullable=False)
    provider = Column(String(64), nullable=False, default="matching-service")
    algorithm_versions = Column(JSON, nullable=False, default=dict)
    data_versions = Column(JSON, nullable=False, default=dict)
    versions = Column(JSON, nullable=False, default=dict)
    resume_id = Column(String(36), nullable=True)
    validated_cv_snapshot_id = Column(String(36), nullable=True)
    position_id = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
