from uuid import uuid4

from sqlalchemy import CheckConstraint, Column, Date, DateTime, Float, JSON, String, Text, UniqueConstraint

from app.core.database import Base
from app.models.user import utc_now


TREND_SOURCE_TYPES = ("policy", "report", "paper", "webpage")


class TrendSource(Base):
    __tablename__ = "trend_sources"
    __table_args__ = (
        CheckConstraint(
            f"source_type in {TREND_SOURCE_TYPES}",
            name="ck_trend_sources_type_allowed",
        ),
        UniqueConstraint("provider_run_id", "snapshot_reference", name="uq_trend_sources_provider_snapshot"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_type = Column(String(32), nullable=False, index=True)
    provider_run_id = Column(String(80), nullable=True, index=True)
    external_source_id = Column(String(256), nullable=True)
    source_version = Column(String(128), nullable=True)
    captured_at = Column(DateTime(timezone=True), nullable=True)
    snapshot_reference = Column(String(128), nullable=True)
    extraction_version = Column(String(128), nullable=True)
    source_metadata = Column(JSON, nullable=False, default=dict)
    title = Column(String(255), nullable=False)
    source_name = Column(String(255), nullable=True)
    url = Column(String(1024), nullable=True)
    raw_text = Column(Text, nullable=False)
    publish_date = Column(Date, nullable=True)
    credibility_score = Column(Float, nullable=False, default=0.8)
    parsed_keywords = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
