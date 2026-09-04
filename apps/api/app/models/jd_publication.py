from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, JSON, String, UniqueConstraint, event

from app.core.database import Base
from app.models.user import utc_now


class JDPublication(Base):
    __tablename__ = "jd_publications"
    __table_args__ = (
        UniqueConstraint("parse_result_id", name="uq_jd_publications_parse_result_id"),
        UniqueConstraint("idempotency_key", name="uq_jd_publications_idempotency_key"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    parse_result_id = Column(
        String(36),
        ForeignKey("jd_parse_results.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    jd_id = Column(
        String(36),
        ForeignKey("job_descriptions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_jd_id = Column(
        String(36), ForeignKey("source_jds.id", ondelete="RESTRICT"), nullable=True
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
    )
    document_id = Column(String(128), nullable=False)
    schema_version = Column(String(32), nullable=False)
    normalization_schema_version = Column(String(32), nullable=False)
    idempotency_key = Column(String(180), nullable=False)
    snapshot_payload = Column(JSON, nullable=False)
    published_by = Column(String(36), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


def _reject_publication_mutation(mapper, connection, target) -> None:
    raise ValueError("JDPublication records are immutable")


event.listen(JDPublication, "before_update", _reject_publication_mutation)
event.listen(JDPublication, "before_delete", _reject_publication_mutation)
