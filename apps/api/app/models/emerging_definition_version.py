from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, JSON, String

from app.core.database import Base
from app.models.user import utc_now


class EmergingDefinitionVersion(Base):
    __tablename__ = "emerging_definition_versions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    emerging_id = Column(
        String(36), ForeignKey("emerging_positions.id"), nullable=False, index=True
    )
    snapshot = Column(JSON, nullable=False)
    selected = Column(Boolean, nullable=False, default=False, index=True)
    created_by = Column(String(36), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
