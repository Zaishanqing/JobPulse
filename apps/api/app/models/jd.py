from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, Column, Date, DateTime, Float, ForeignKey, String, Text

from app.core.database import Base
from app.models.user import utc_now


JD_PARSE_STATUSES = ("pending", "running", "completed", "failed")


class JobDescription(Base):
    __tablename__ = "job_descriptions"
    __table_args__ = (
        CheckConstraint(
            f"parse_status in {JD_PARSE_STATUSES}",
            name="ck_job_descriptions_parse_status_allowed",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_type = Column(String(64), nullable=False)
    source_name = Column(String(128), nullable=True)
    enterprise_id = Column(String(36), nullable=True, index=True)
    title = Column(String(255), nullable=False)
    raw_text = Column(Text, nullable=False)
    cleaned_text = Column(Text, nullable=True)
    publish_date = Column(Date, nullable=True)
    url = Column(String(512), nullable=True)
    file_id = Column(String(36), ForeignKey("file_assets.id"), nullable=True)
    source_jd_id = Column(
        String(36), ForeignKey("source_jds.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    source_jd_version_id = Column(
        String(36),
        ForeignKey("source_jd_versions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    extraction_task_id = Column(
        String(36),
        ForeignKey("extraction_tasks.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
        index=True,
    )
    source_document_id = Column(String(128), nullable=True, index=True)
    extraction_bundle_version = Column(String(64), nullable=True)
    parse_status = Column(String(32), nullable=False, default="pending")
    input_extraction_status = Column(String(32), nullable=False, default="not_required")
    input_provider = Column(String(64), nullable=True)
    input_error_code = Column(String(128), nullable=True)
    input_error_message = Column(Text, nullable=True)
    copy_risk_score = Column(Float, nullable=True)
    inflation_score = Column(Float, nullable=True)
    is_downweighted = Column(Boolean, nullable=False, default=False)
    is_deprecated = Column(
        Boolean, nullable=False, default=False, server_default="false", index=True
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
