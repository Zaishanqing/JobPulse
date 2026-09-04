from uuid import uuid4

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.types import JSON

from app.core.database import Base
from app.models.user import utc_now


class OutboxMessage(Base):
    __tablename__ = "outbox_messages"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_outbox_event_id"),
        UniqueConstraint("idempotency_key", name="uq_outbox_idempotency_key"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    event_id = Column(String(36), nullable=False)
    event_type = Column(String(120), nullable=False)
    aggregate_id = Column(String(120), nullable=False, index=True)
    idempotency_key = Column(String(180), nullable=False)
    payload = Column(JSON, nullable=False)
    status = Column(String(24), nullable=False, default="pending", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    lease_owner = Column(String(80), nullable=True)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    trace_id = Column(String(64), nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
