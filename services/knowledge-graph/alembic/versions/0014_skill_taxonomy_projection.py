"""store authoritative multidimensional skill taxonomy projection

Revision ID: 0014_skill_taxonomy_projection
Revises: 0013_k0_governance_effects
"""

import sqlalchemy as sa
from alembic import op


revision = "0014_skill_taxonomy_projection"
down_revision = "0013_k0_governance_effects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("skills") as batch:
        batch.alter_column("category_code", existing_type=sa.String(80), nullable=True)
        batch.add_column(sa.Column("taxonomy_version", sa.String(71), nullable=True))
        batch.create_index("ix_skills_taxonomy_version", ["taxonomy_version"])
    op.create_table(
        "skill_taxonomy_nodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("facet", sa.String(32), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name_zh", sa.String(120), nullable=False),
        sa.Column("name_en", sa.String(120), nullable=True),
        sa.UniqueConstraint("facet", "code"),
    )
    op.create_index("ix_skill_taxonomy_nodes_facet", "skill_taxonomy_nodes", ["facet"])
    op.create_table(
        "skill_classifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("skill_id", sa.String(80), sa.ForeignKey("skills.skill_id"), nullable=False),
        sa.Column("taxonomy_node_id", sa.Integer(), sa.ForeignKey("skill_taxonomy_nodes.id"), nullable=False),
        sa.Column("facet", sa.String(32), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("skill_id", "taxonomy_node_id"),
    )
    op.create_index("ix_skill_classifications_skill_id", "skill_classifications", ["skill_id"])
    op.create_index("ix_skill_classifications_facet", "skill_classifications", ["facet"])
    op.create_index(
        "uq_kg_skill_classification_singleton",
        "skill_classifications",
        ["skill_id", "facet"],
        unique=True,
        sqlite_where=sa.text("facet IN ('concept_class', 'technology_kind')"),
        postgresql_where=sa.text("facet IN ('concept_class', 'technology_kind')"),
    )


def downgrade() -> None:
    op.drop_table("skill_classifications")
    op.drop_table("skill_taxonomy_nodes")
    with op.batch_alter_table("skills") as batch:
        batch.drop_index("ix_skills_taxonomy_version")
        batch.drop_column("taxonomy_version")
        batch.alter_column("category_code", existing_type=sa.String(80), nullable=False)
