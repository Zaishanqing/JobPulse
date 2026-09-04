from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)

from app.core.database import Base
from app.models.user import utc_now


class PredictedPositionMatch(Base):
    __tablename__ = "predicted_position_matches"
    __table_args__ = (
        UniqueConstraint(
            "predicted_position_id",
            "version",
            "target_type",
            "target_id",
            name="uq_prediction_match_version_target",
        ),
        CheckConstraint(
            "recommendation in ('new_candidate', 'possible_duplicate', "
            "'possible_evolution', 'insufficient_evidence')",
            name="ck_prediction_match_recommendation",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    predicted_position_id = Column(
        String(36), ForeignKey("predicted_positions.id"), nullable=False, index=True
    )
    version = Column(Integer, nullable=False)
    target_type = Column(String(32), nullable=False)
    target_id = Column(String(64), nullable=False)
    similarity_score = Column(Float, nullable=False)
    matched_skills = Column(JSON, nullable=False, default=list)
    missing_skills = Column(JSON, nullable=False, default=list)
    overlap_evidence = Column(JSON, nullable=False, default=dict)
    recommendation = Column(String(32), nullable=False)
    created_by = Column(String(36), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    cache_key = Column(String(128), nullable=False, default="legacy")


class PredictedPositionDefinitionVersion(Base):
    __tablename__ = "predicted_position_definition_versions"
    __table_args__ = (
        UniqueConstraint(
            "predicted_position_id",
            "version",
            name="uq_prediction_definition_version",
        ),
        CheckConstraint(
            "status in ('draft', 'in_review', 'approved', 'rejected', 'published')",
            name="ck_prediction_definition_status",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    predicted_position_id = Column(
        String(36), ForeignKey("predicted_positions.id"), nullable=False, index=True
    )
    version = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="draft")
    definition_payload = Column(JSON, nullable=False)
    review_task_id = Column(String(36), ForeignKey("review_tasks.id"), nullable=True)
    created_by = Column(String(36), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    cache_key = Column(String(128), nullable=False, default="legacy")


class PredictedPositionRelationVersion(Base):
    __tablename__ = "predicted_position_relation_versions"
    __table_args__ = (
        UniqueConstraint(
            "predicted_position_id",
            "version",
            name="uq_prediction_relation_version",
        ),
        CheckConstraint(
            "relation_type in ('standard_position', 'emerging_position', 'independent')",
            name="ck_prediction_relation_type",
        ),
        CheckConstraint(
            "status in ('active', 'deleted')",
            name="ck_prediction_relation_status",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    predicted_position_id = Column(
        String(36), ForeignKey("predicted_positions.id"), nullable=False, index=True
    )
    version = Column(Integer, nullable=False)
    relation_type = Column(String(32), nullable=False)
    target_id = Column(String(64), nullable=True)
    status = Column(String(16), nullable=False, default="active")
    reason = Column(String(1000), nullable=True)
    created_by = Column(String(36), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    relation_identity_id = Column(
        String(36),
        nullable=False,
        index=True,
        default=lambda: str(uuid4()),
    )
    supersedes_relation_id = Column(String(36), nullable=True)
