from uuid import uuid4

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, String, Text

from app.core.database import Base
from app.models.user import utc_now


RESUME_SOURCE_TYPES = ("text", "file", "image")
RESUME_PARSE_STATUSES = ("pending", "running", "completed", "failed")


class Resume(Base):
    __tablename__ = "resumes"
    __table_args__ = (
        CheckConstraint(
            f"source_type in {RESUME_SOURCE_TYPES}",
            name="ck_resumes_source_type_allowed",
        ),
        CheckConstraint(
            f"parse_status in {RESUME_PARSE_STATUSES}",
            name="ck_resumes_parse_status_allowed",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    source_type = Column(String(32), nullable=False)
    file_id = Column(String(36), ForeignKey("file_assets.id"), nullable=True)
    display_name = Column(String(120), nullable=True)
    original_filename = Column(String(255), nullable=True)
    source_cv_version_id = Column(
        String(36),
        ForeignKey("source_cv_versions.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
        index=True,
    )
    validated_cv_snapshot_id = Column(
        String(36),
        ForeignKey("validated_cv_snapshots.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    raw_text = Column(Text, nullable=False, default="")
    parse_status = Column(String(32), nullable=False, default="pending")
    input_extraction_status = Column(String(32), nullable=False, default="not_required")
    input_provider = Column(String(64), nullable=True)
    input_error_code = Column(String(128), nullable=True)
    input_error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
