from uuid import uuid4

from sqlalchemy import Column, Date, DateTime, Float, String, Text

from app.core.database import Base
from app.models.user import utc_now


class EvidenceSource(Base):
    __tablename__ = "evidence_sources"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_type = Column(String(64), nullable=False, index=True)
    source_name = Column(String(255), nullable=True)
    source_platform = Column(String(128), nullable=True)
    title = Column(String(255), nullable=False)
    url = Column(String(1024), nullable=True)
    raw_text = Column(Text, nullable=True)
    publish_date = Column(Date, nullable=True)
    credibility_score = Column(Float, nullable=False, default=0.8)
    related_object_type = Column(String(64), nullable=True, index=True)
    related_object_id = Column(String(64), nullable=True, index=True)
    enterprise_id = Column(String(128), nullable=True)
    template_cluster_id = Column(String(128), nullable=True)
    source_version = Column(String(128), nullable=True)
    source_fact_id = Column(String(128), nullable=True)
    source_jd_id = Column(String(128), nullable=True)
    # TEMP-LAG lineage: the immutable SourceJDVersion.id this governance row was
    # derived from.  It is independent from ``source_version`` (source-fact
    # version) and is the only field used to fetch crawler ``crawl_time``.
    source_jd_version_id = Column(String(36), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
