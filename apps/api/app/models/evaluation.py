from uuid import uuid4

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, JSON, String, Text

from app.core.database import Base
from app.models.user import utc_now


EVALUATION_DATASET_TYPES = ("jd", "resume", "match")
EVALUATION_REPORT_TYPES = (
    "jd_parse",
    "resume_parse",
    "match",
    "skill_normalization",
    "cluster",
)


class EvaluationDataset(Base):
    __tablename__ = "evaluation_datasets"
    __table_args__ = (
        CheckConstraint(
            f"dataset_type in {EVALUATION_DATASET_TYPES}",
            name="ck_evaluation_datasets_type_allowed",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    dataset_type = Column(String(32), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


class EvaluationReport(Base):
    __tablename__ = "evaluation_reports"
    __table_args__ = (
        CheckConstraint(
            f"report_type in {EVALUATION_REPORT_TYPES}",
            name="ck_evaluation_reports_type_allowed",
        ),
        CheckConstraint(
            "evaluation_status in ('completed', 'insufficient_data')",
            name="ck_evaluation_reports_status_allowed",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    report_type = Column(String(64), nullable=False, index=True)
    dataset_id = Column(
        String(36),
        ForeignKey("evaluation_datasets.id"),
        nullable=True,
        index=True,
    )
    metrics = Column(JSON, nullable=False, default=dict)
    error_cases = Column(JSON, nullable=False, default=list)
    evaluation_status = Column(String(32), nullable=False, default="insufficient_data")
    algorithm_version = Column(String(128), nullable=False, default="rule-eval-v1")
    config_snapshot = Column(JSON, nullable=False, default=dict)
    evaluated_count = Column(Integer, nullable=False, default=0)
    error_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
