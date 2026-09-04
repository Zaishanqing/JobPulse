from uuid import uuid4

from sqlalchemy import CheckConstraint, Column, DateTime, Float, JSON, String, UniqueConstraint

from app.core.database import Base
from app.models.user import utc_now


PREDICTED_POSITION_STATUSES = ("candidate", "verified", "published", "rejected")


class PredictedPosition(Base):
    __tablename__ = "predicted_positions"
    __table_args__ = (
        CheckConstraint(
            f"status in {PREDICTED_POSITION_STATUSES}",
            name="ck_predicted_positions_status_allowed",
        ),
        UniqueConstraint("provider_run_id", "candidate_key", name="uq_predicted_positions_provider_candidate"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    position_name = Column(String(255), nullable=False)
    provider_run_id = Column(String(80), nullable=True, index=True)
    candidate_key = Column(String(128), nullable=True)
    industry_domain = Column(String(255), nullable=True)
    emergence_score = Column(Float, nullable=True)
    score_components = Column(JSON, nullable=False, default=dict)
    algorithm_version = Column(String(128), nullable=True)
    formula_version = Column(String(128), nullable=True)
    window_start = Column(DateTime(timezone=True), nullable=True)
    window_end = Column(DateTime(timezone=True), nullable=True)
    source_coverage = Column(Float, nullable=True)
    missing_sources = Column(JSON, nullable=False, default=list)
    quality_flags = Column(JSON, nullable=False, default=list)
    evidence_references = Column(JSON, nullable=False, default=list)
    published_definition_version_id = Column(String(36), nullable=True, index=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    prediction_basis = Column(JSON, nullable=False, default=list)
    related_source_ids = Column(JSON, nullable=False, default=list)
    potential_responsibilities = Column(JSON, nullable=False, default=list)
    potential_skills = Column(JSON, nullable=False, default=list)
    industry_scenarios = Column(JSON, nullable=False, default=list)
    confidence_score = Column(Float, nullable=False, default=0.0)
    status = Column(String(32), nullable=False, default="candidate")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
