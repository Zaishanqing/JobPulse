from uuid import uuid4

from sqlalchemy import Column, DateTime, Integer, String

from app.core.database import Base
from app.models.user import utc_now


class FileAsset(Base):
    __tablename__ = "file_assets"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_user_id = Column(String(36), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    content_type = Column(String(128), nullable=True)
    path = Column(String(512), nullable=False)
    size = Column(Integer, nullable=False, default=0)
    purpose = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
