from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, JSON, String, Text

from app.core.database import Base
from app.models.user import utc_now

RAG_GENERATION_STATUSES = ("draft", "confirmed")


class RagGeneration(Base):
    __tablename__ = "rag_generations"
    __table_args__ = (
        CheckConstraint(
            f"status in {RAG_GENERATION_STATUSES}",
            name="ck_rag_generations_status_allowed",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    prompt = Column(Text, nullable=False)
    text = Column(Text, nullable=False, default="")
    evidence_ids = Column(JSON, nullable=False, default=list)
    citations = Column(JSON, nullable=False, default=list)
    need_review = Column(Boolean, nullable=False, default=True)
    status = Column(String(32), nullable=False, default="draft")
    created_by = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    confirmed_by = Column(String(36), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
