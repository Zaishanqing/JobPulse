from uuid import uuid4

from sqlalchemy import CheckConstraint, Column, Date, DateTime, Float, ForeignKey, JSON, String, Text, UniqueConstraint

from app.core.database import Base
from app.models.user import utc_now


TREND_REPORT_STATUSES = ("draft", "published")


class TrendReport(Base):
    __tablename__ = "trend_reports"
    __table_args__ = (
        CheckConstraint(
            f"status in {TREND_REPORT_STATUSES}",
            name="ck_trend_reports_status_allowed",
        ),
        UniqueConstraint(
            "provider_run_id", "position_id", "graph_version_id",
            name="uq_trend_report_provider_position_graph",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    position_id = Column(
        String(36),
        ForeignKey("standard_positions.id"),
        nullable=False,
        index=True,
    )
    graph_version_id = Column(String(36), nullable=True, index=True)
    time_window_start = Column(Date, nullable=True)
    time_window_end = Column(Date, nullable=True)
    current_graph = Column(JSON, nullable=False, default=dict)
    skill_weight_distribution = Column(JSON, nullable=False, default=dict)
    new_skills = Column(JSON, nullable=False, default=list)
    rising_skills = Column(JSON, nullable=False, default=list)
    declining_skills = Column(JSON, nullable=False, default=list)
    replaced_skills = Column(JSON, nullable=False, default=list)
    skill_combo_shifts = Column(JSON, nullable=False, default=list)
    risks = Column(JSON, nullable=False, default=list)
    summary = Column(Text, nullable=True)
    provider_run_id = Column(String(80), nullable=True, index=True)
    algorithm_version = Column(String(128), nullable=True)
    formula_version = Column(String(128), nullable=True)
    skill_catalog_version = Column(String(128), nullable=True)
    source_coverage = Column(Float, nullable=True)
    missing_sources = Column(JSON, nullable=False, default=list)
    quality_flags = Column(JSON, nullable=False, default=list)
    evidence_references = Column(JSON, nullable=False, default=list)
    unresolved_terms = Column(JSON, nullable=False, default=list)
    skill_trend_details = Column(JSON, nullable=False, default=list)
    status = Column(String(32), nullable=False, default="draft")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


class TrendReportReviewAdjustment(Base):
    __tablename__ = "trend_report_review_adjustments"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    report_id = Column(
        String(36), ForeignKey("trend_reports.id"), nullable=False, index=True
    )
    actor_user_id = Column(String(36), nullable=False, index=True)
    reason = Column(Text, nullable=False)
    before_values = Column(JSON, nullable=False)
    after_values = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
