from uuid import uuid4

from sqlalchemy import Column, DateTime, JSON, String, UniqueConstraint

from app.core.database import Base
from app.models.user import utc_now


class StandardPosition(Base):
    __tablename__ = "standard_positions"
    __table_args__ = (
        UniqueConstraint(
            "source_emerging_position_id",
            name="uq_standard_positions_source_emerging_position_id",
        ),
        UniqueConstraint("position_code", name="uq_standard_positions_position_code"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    position_code = Column(String(100), nullable=True, index=True)
    position_name = Column(String(255), nullable=False)
    taxonomy_family_code = Column(String(80), nullable=True, index=True)
    taxonomy_family_name = Column(String(120), nullable=True)
    skill_domain_codes = Column(JSON, nullable=False, default=list)
    definition = Column(String(1000), nullable=False, default="")
    aliases = Column(JSON, nullable=False, default=list)
    include_when = Column(JSON, nullable=False, default=list)
    exclude_when = Column(JSON, nullable=False, default=list)
    confusable_with = Column(JSON, nullable=False, default=list)
    taxonomy_version = Column(String(64), nullable=False, default="position-taxonomy.v3.0.0")
    lifecycle_status = Column(String(32), nullable=False, default="active")
    deprecated_at = Column(DateTime(timezone=True), nullable=True)
    replaced_by = Column(String(100), nullable=True)
    sample_support_status = Column(String(16), nullable=False, default="none")
    source_emerging_position_id = Column(String(36), nullable=True, index=True)
    core_responsibilities = Column(JSON, nullable=False, default=list)
    required_skills = Column(JSON, nullable=False, default=list)
    bonus_skills = Column(JSON, nullable=False, default=list)
    industry_scenarios = Column(JSON, nullable=False, default=list)
    status = Column(String(32), nullable=False, default="existing")
    graph_onboarding_status = Column(
        String(32), nullable=False, default="mapping_required"
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
