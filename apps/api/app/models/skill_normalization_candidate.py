from uuid import uuid4

from sqlalchemy import CheckConstraint, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import validates

from app.core.database import Base
from app.domain.skills import normalize_skill_expression
from app.models.user import utc_now


NORMALIZATION_CANDIDATE_STATUSES = (
    "pending",
    "mapped_existing",
    "created_new",
    "excluded_non_skill",
    "deferred",
)
NORMALIZATION_CANDIDATE_SOURCE_TYPES = ("jd", "cv", "manual", "unknown")


class SkillNormalizationCandidate(Base):
    __tablename__ = "skill_normalization_candidates"
    __table_args__ = (
        CheckConstraint(
            f"status in {NORMALIZATION_CANDIDATE_STATUSES}",
            name="ck_skill_normalization_candidates_status_allowed",
        ),
        CheckConstraint(
            f"source_type in {NORMALIZATION_CANDIDATE_SOURCE_TYPES}",
            name="ck_skill_normalization_candidates_source_type_allowed",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    raw_skill = Column(String(128), nullable=False, index=True)
    normalized_skill = Column(String(256), nullable=False, index=True)
    candidate_skill_id = Column(String(36), ForeignKey("skills.id"), nullable=True)
    confidence = Column(Float, nullable=False, default=0.0)
    context = Column(Text, nullable=True)
    occurrence_count = Column(Integer, nullable=False, default=1)
    source_type = Column(String(16), nullable=False, default="unknown")
    evidence_samples = Column(JSON, nullable=False, default=list)
    status = Column(String(32), nullable=False, default="pending")
    first_seen_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    last_seen_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    reviewer_id = Column(String(36), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    decision_reason = Column(Text, nullable=True)
    normalization_catalog_version = Column(String(64), nullable=True)
    normalized_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    @validates("raw_skill")
    def _normalize_raw_skill(self, _key: str, value: str) -> str:
        self.normalized_skill = normalize_skill_expression(value)
        return value
