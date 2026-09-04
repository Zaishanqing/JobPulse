from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text

from app.core.database import Base
from app.models.user import utc_now


class OCRResult(Base):
    __tablename__ = "ocr_results"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_type = Column(String(32), nullable=False)
    filename = Column(String(255), nullable=True)
    status = Column(String(32), nullable=False)
    text = Column(Text, nullable=True)
    provider = Column(String(64), nullable=False)
    error_code = Column(String(128), nullable=True)
    error_message = Column(Text, nullable=True)
    created_by = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    edited = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
