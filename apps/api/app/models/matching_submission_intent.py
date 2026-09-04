from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
)

from app.core.database import Base
from app.models.user import utc_now


class MatchingSubmissionIntent(Base):
    __tablename__ = "matching_submission_intents"
    __table_args__ = (
        CheckConstraint(
            "status IN ("
            "'intended','rejected','remote_unknown','reference_pending',"
            "'reference_saved','abandoned'"
            ")",
            name="ck_matching_submission_intents_status",
        ),
        UniqueConstraint(
            "idempotency_key", name="uq_intent_idempotency_key"
        ),
        Index("ix_intent_status_next_retry", "status", "next_retry_at"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    idempotency_key = Column(String(512), nullable=False)
    user_id = Column(String(36), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False)
    resume_id = Column(String(36), nullable=False)
    position_id = Column(String(200), nullable=False)
    target_type = Column(String(64), nullable=False, default="enterprise_job")
    cv_profile_version = Column(String(200), nullable=False)
    position_profile_version = Column(String(200), nullable=False)
    status = Column(String(32), nullable=False, default="intended")
    retry_count = Column(Integer, nullable=False, default=0)
    last_error_code = Column(String(128), nullable=True)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    schema_version = Column(String(64), nullable=False, default="matching-submission-intent.v1")
    access_scope = Column(String(200), nullable=False, default="")
    source_version = Column(String(512), nullable=False, default="legacy-unspecified")
    taxonomy_version = Column(String(512), nullable=False, default="legacy-unspecified")
    graph_version = Column(String(255), nullable=False, default="legacy-unspecified")
    algorithm_version = Column(String(255), nullable=False, default="legacy-unspecified")
