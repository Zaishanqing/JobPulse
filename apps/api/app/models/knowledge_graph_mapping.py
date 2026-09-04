from uuid import uuid4

from sqlalchemy import CheckConstraint, Column, DateTime, String, Text, UniqueConstraint

from app.core.database import Base
from app.models.user import utc_now


ENTITY_TYPES = ("document", "position", "skill", "graph_version")


class KnowledgeGraphEntityMapping(Base):
    __tablename__ = "knowledge_graph_entity_mappings"
    __table_args__ = (
        UniqueConstraint(
            "entity_type", "main_system_id", name="uq_kg_mapping_entity_main_id"
        ),
        CheckConstraint(
            f"entity_type in {ENTITY_TYPES}", name="ck_kg_mapping_entity_type"
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    entity_type = Column(String(32), nullable=False, index=True)
    main_system_id = Column(String(80), nullable=False, index=True)
    knowledge_graph_id = Column(String(80), nullable=True, index=True)
    sync_version = Column("payload_hash", String(200), nullable=True)
    sync_status = Column(String(64), nullable=False, default="pending")
    last_error_code = Column(String(64), nullable=True)
    last_error_message = Column(Text, nullable=True)
    last_trace_id = Column(String(64), nullable=True)
    synced_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
