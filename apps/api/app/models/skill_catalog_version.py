from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, event

from app.core.database import Base
from app.models.user import utc_now


class SkillCatalogVersion(Base):
    __tablename__ = "skill_catalog_versions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    version_number = Column(Integer, nullable=False, unique=True, index=True)
    catalog_version = Column(String(64), nullable=False, unique=True, index=True)
    snapshot = Column(JSON, nullable=False)
    change_summary = Column(JSON, nullable=False)
    published_by = Column(String(36), ForeignKey("users.id"), nullable=False)
    published_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


def _reject_catalog_version_mutation(mapper, connection, target) -> None:
    raise ValueError("SkillCatalogVersion records are immutable")


event.listen(SkillCatalogVersion, "before_update", _reject_catalog_version_mutation)
event.listen(SkillCatalogVersion, "before_delete", _reject_catalog_version_mutation)
