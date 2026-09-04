from sqlalchemy import Column, DateTime, ForeignKey, JSON, String

from app.core.database import Base
from app.models.user import utc_now


class CVPositionClassification(Base):
    __tablename__ = "cv_position_classifications"

    resume_id = Column(
        String(36),
        ForeignKey("resumes.id", ondelete="CASCADE"),
        primary_key=True,
    )
    taxonomy_version = Column(String(64), nullable=False)
    classifications = Column(JSON, nullable=False)
    source_run_ids = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
