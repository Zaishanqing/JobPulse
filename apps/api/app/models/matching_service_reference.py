from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, String, UniqueConstraint

from app.core.database import Base
from app.models.user import utc_now


class MatchingServiceReference(Base):
    __tablename__ = "matching_service_references"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "idempotency_key", name="uq_matching_service_reference_idempotency"
        ),
        Index("ix_matching_service_reference_user_created", "user_id", "created_at"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    task_id = Column(String(200), nullable=False, unique=True, index=True)
    evaluation_id = Column(String(200), nullable=True, unique=True, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False)
    resume_id = Column(String(36), ForeignKey("resumes.id", ondelete="RESTRICT"), nullable=False)
    position_id = Column(String(64), nullable=False)
    provider = Column(String(64), nullable=False, default="matching-service")
    target_type = Column(String(64), nullable=False, default="standard_position")
    status = Column(String(32), nullable=False)
    idempotency_key = Column(String(512), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
    schema_version = Column(String(64), nullable=False, default="matching-service-reference.v1")
    access_scope = Column(String(200), nullable=False, default="")
    source_version = Column(String(512), nullable=False, default="legacy-unspecified")
    cv_profile_version = Column(String(200), nullable=False, default="")
    position_profile_version = Column(String(200), nullable=False, default="")
    taxonomy_version = Column(String(512), nullable=False, default="legacy-unspecified")
    graph_version = Column(String(255), nullable=False, default="legacy-unspecified")
    algorithm_version = Column(String(255), nullable=False, default="legacy-unspecified")
    matching_method = Column(String(32), nullable=True)
    degraded = Column(Boolean, nullable=True)
    overall_score = Column(Float, nullable=True)
    error_code = Column(String(64), nullable=True)
    error_message = Column(String(500), nullable=True)
