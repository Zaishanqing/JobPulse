from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)

from app.core.database import Base
from app.models.user import utc_now


SKILL_TAXONOMY_FACETS = (
    "concept_class",
    "technology_kind",
    "domain",
)
SKILL_TAXONOMY_NODE_STATUSES = ("active", "inactive")


class SkillTaxonomyNode(Base):
    __tablename__ = "skill_taxonomy_nodes"
    __table_args__ = (
        UniqueConstraint(
            "facet",
            "code",
            name="uq_skill_taxonomy_nodes_facet_code",
        ),
        UniqueConstraint(
            "id",
            "facet",
            name="uq_skill_taxonomy_nodes_id_facet",
        ),
        CheckConstraint(
            f"facet in {SKILL_TAXONOMY_FACETS}",
            name="ck_skill_taxonomy_nodes_facet_allowed",
        ),
        CheckConstraint(
            f"status in {SKILL_TAXONOMY_NODE_STATUSES}",
            name="ck_skill_taxonomy_nodes_status_allowed",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    facet = Column(String(32), nullable=False, index=True)
    code = Column(String(80), nullable=False)
    name_zh = Column(String(120), nullable=False)
    name_en = Column(String(120), nullable=True)
    parent_id = Column(
        String(36),
        ForeignKey("skill_taxonomy_nodes.id"),
        nullable=True,
        index=True,
    )
    status = Column(String(16), nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


class SkillClassification(Base):
    __tablename__ = "skill_classifications"
    __table_args__ = (
        UniqueConstraint(
            "skill_id",
            "taxonomy_node_id",
            name="uq_skill_classifications_skill_node",
        ),
        CheckConstraint(
            f"facet in {SKILL_TAXONOMY_FACETS}",
            name="ck_skill_classifications_facet_allowed",
        ),
        ForeignKeyConstraint(
            ["taxonomy_node_id", "facet"],
            ["skill_taxonomy_nodes.id", "skill_taxonomy_nodes.facet"],
            name="fk_skill_classifications_node_facet",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_skill_classifications_singleton_facet",
            "skill_id",
            "facet",
            unique=True,
            sqlite_where=text(
                "facet IN ('concept_class', 'technology_kind')"
            ),
            postgresql_where=text(
                "facet IN ('concept_class', 'technology_kind')"
            ),
        ),
        Index(
            "uq_skill_classifications_primary_facet",
            "skill_id",
            "facet",
            unique=True,
            sqlite_where=text("is_primary = 1"),
            postgresql_where=text("is_primary"),
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    skill_id = Column(
        String(36),
        ForeignKey("skills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    taxonomy_node_id = Column(String(36), nullable=False, index=True)
    facet = Column(String(32), nullable=False, index=True)
    is_primary = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
