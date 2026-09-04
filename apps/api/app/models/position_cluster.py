from uuid import uuid4

from sqlalchemy import CheckConstraint, Column, Date, DateTime, Float, JSON, Integer, String

from app.core.database import Base
from app.models.user import utc_now


POSITION_CLUSTER_STATUSES = ("active", "archived")


class PositionCluster(Base):
    __tablename__ = "position_clusters"
    __table_args__ = (
        CheckConstraint(
            f"status in {POSITION_CLUSTER_STATUSES}",
            name="ck_position_clusters_status_allowed",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    cluster_name = Column(String(255), nullable=False)
    algorithm = Column(String(128), nullable=False, default="multi_view")
    time_window_start = Column(Date, nullable=True)
    time_window_end = Column(Date, nullable=True)
    sample_count = Column(Integer, nullable=False, default=0)
    core_skills = Column(JSON, nullable=False, default=list)
    representative_titles = Column(JSON, nullable=False, default=list)
    representative_jd_ids = Column(JSON, nullable=False, default=list)
    stability_score = Column(Float, nullable=False, default=0.0)
    growth_score = Column(Float, nullable=False, default=0.0)
    distance_from_existing_positions = Column(Float, nullable=False, default=0.0)
    discovery_run_id = Column(String(36), nullable=True, index=True)
    discovery_run_status = Column(String(32), nullable=True)
    discovery_assessment = Column(JSON, nullable=False, default=dict)
    generated_definition = Column(JSON, nullable=False, default=dict)
    discovery_lineages = Column(JSON, nullable=False, default=list)
    status = Column(String(32), nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
