from uuid import uuid4

from sqlalchemy import CheckConstraint, Column, DateTime, Float, ForeignKey, JSON, String, UniqueConstraint

from app.core.database import Base
from app.models.user import utc_now


EMERGING_POSITION_STATUSES = (
    "draft",
    "pending_review",
    "approved",
    "published",
    "rejected",
)


class EmergingPosition(Base):
    __tablename__ = "emerging_positions"
    __table_args__ = (
        CheckConstraint(
            f"status in {EMERGING_POSITION_STATUSES}",
            name="ck_emerging_positions_status_allowed",
        ),
        UniqueConstraint("cluster_id", name="uq_emerging_positions_cluster_id"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    cluster_id = Column(
        String(36),
        ForeignKey("position_clusters.id"),
        nullable=False,
        index=True,
    )
    position_name = Column(String(255), nullable=False)
    core_responsibilities = Column(JSON, nullable=False, default=list)
    required_skills = Column(JSON, nullable=False, default=list)
    bonus_skills = Column(JSON, nullable=False, default=list)
    industry_scenarios = Column(JSON, nullable=False, default=list)
    germination_score = Column(Float, nullable=True)
    score_dimensions = Column(JSON, nullable=False, default=dict)
    evidence_jd_ids = Column(JSON, nullable=False, default=list)
    field_evidence = Column(JSON, nullable=False, default=dict)
    review_history = Column(JSON, nullable=False, default=list)
    published_snapshot = Column(JSON, nullable=True)
    status = Column(String(32), nullable=False, default="draft")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
