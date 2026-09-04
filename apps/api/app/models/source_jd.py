from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, JSON, String, Text, UniqueConstraint, event

from app.core.database import Base
from app.models.user import utc_now


class SourceJD(Base):
    __tablename__ = "source_jds"
    __table_args__ = (
        UniqueConstraint(
            "source_platform",
            "source_record_id",
            name="uq_source_jds_platform_record",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_platform = Column(String(64), nullable=False, index=True)
    source_record_id = Column(String(255), nullable=False)
    latest_version_id = Column(
        String(36),
        ForeignKey(
            "source_jd_versions.id",
            name="fk_source_jds_latest_version_id",
            use_alter=True,
        ),
        nullable=True,
        index=True,
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


class SourceJDVersion(Base):
    __tablename__ = "source_jd_versions"
    __table_args__ = (
        UniqueConstraint(
            "source_jd_id",
            "source_version",
            name="uq_source_jd_versions_source_version",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_jd_id = Column(
        String(36),
        ForeignKey("source_jds.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_version = Column(String(128), nullable=False)
    schema_version = Column(String(32), nullable=False)
    raw_text = Column(Text, nullable=False)
    content_hash = Column(String(71), nullable=False)
    raw_payload = Column(JSON, nullable=False)
    raw_html = Column(Text, nullable=True)
    source_url = Column(String(2048), nullable=True)
    crawl_time = Column(DateTime(timezone=True), nullable=False)
    job_title_raw = Column(String(512), nullable=True)
    company_name_raw = Column(String(512), nullable=True)
    region_raw = Column(String(255), nullable=True)
    publish_time_raw = Column(String(255), nullable=True)
    text_canonicalization_version = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, index=True)


def _reject_version_mutation(mapper, connection, target) -> None:
    raise ValueError("SourceJDVersion records are immutable")


event.listen(SourceJDVersion, "before_update", _reject_version_mutation)
event.listen(SourceJDVersion, "before_delete", _reject_version_mutation)
