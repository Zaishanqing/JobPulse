"""add multidimensional classifications for standard skills

Revision ID: 20260728_35
Revises: 20260728_34
"""

import sqlalchemy as sa
from alembic import op


revision = "20260728_35"
down_revision = "20260728_34"
branch_labels = None
depends_on = None


FACETS = "('concept_class', 'technology_kind', 'domain')"
STATUSES = "('active', 'inactive')"


def upgrade() -> None:
    op.create_table(
        "skill_taxonomy_nodes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("facet", sa.String(length=32), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("name_zh", sa.String(length=120), nullable=False),
        sa.Column("name_en", sa.String(length=120), nullable=True),
        sa.Column("parent_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"facet in {FACETS}",
            name="ck_skill_taxonomy_nodes_facet_allowed",
        ),
        sa.CheckConstraint(
            f"status in {STATUSES}",
            name="ck_skill_taxonomy_nodes_status_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["skill_taxonomy_nodes.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "facet",
            "code",
            name="uq_skill_taxonomy_nodes_facet_code",
        ),
        sa.UniqueConstraint(
            "id",
            "facet",
            name="uq_skill_taxonomy_nodes_id_facet",
        ),
    )
    op.create_index(
        "ix_skill_taxonomy_nodes_facet",
        "skill_taxonomy_nodes",
        ["facet"],
    )
    op.create_index(
        "ix_skill_taxonomy_nodes_parent_id",
        "skill_taxonomy_nodes",
        ["parent_id"],
    )

    op.create_table(
        "skill_classifications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("skill_id", sa.String(length=36), nullable=False),
        sa.Column("taxonomy_node_id", sa.String(length=36), nullable=False),
        sa.Column("facet", sa.String(length=32), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"facet in {FACETS}",
            name="ck_skill_classifications_facet_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["skill_id"],
            ["skills.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["taxonomy_node_id", "facet"],
            ["skill_taxonomy_nodes.id", "skill_taxonomy_nodes.facet"],
            name="fk_skill_classifications_node_facet",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "skill_id",
            "taxonomy_node_id",
            name="uq_skill_classifications_skill_node",
        ),
    )
    op.create_index(
        "ix_skill_classifications_skill_id",
        "skill_classifications",
        ["skill_id"],
    )
    op.create_index(
        "ix_skill_classifications_taxonomy_node_id",
        "skill_classifications",
        ["taxonomy_node_id"],
    )
    op.create_index(
        "ix_skill_classifications_facet",
        "skill_classifications",
        ["facet"],
    )
    op.create_index(
        "uq_skill_classifications_singleton_facet",
        "skill_classifications",
        ["skill_id", "facet"],
        unique=True,
        sqlite_where=sa.text(
            "facet IN ('concept_class', 'technology_kind')"
        ),
        postgresql_where=sa.text(
            "facet IN ('concept_class', 'technology_kind')"
        ),
    )
    op.create_index(
        "uq_skill_classifications_primary_facet",
        "skill_classifications",
        ["skill_id", "facet"],
        unique=True,
        sqlite_where=sa.text("is_primary = 1"),
        postgresql_where=sa.text("is_primary"),
    )


def downgrade() -> None:
    op.drop_table("skill_classifications")
    op.drop_table("skill_taxonomy_nodes")
