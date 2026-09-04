from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String

from app.core.database import Base
from app.models.user import utc_now


class SystemConfig(Base):
    __tablename__ = "system_configs"

    name = Column(String(64), primary_key=True)
    config = Column(JSON, nullable=False, default=dict)
    version = Column(Integer, nullable=False, default=1)
    updated_by = Column(String(36), ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
