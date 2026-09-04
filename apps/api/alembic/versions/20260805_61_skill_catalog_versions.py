"""store immutable skill catalog publication snapshots

Revision ID: 20260805_61
Revises: 20260805_60
"""

import sqlalchemy as sa
from alembic import op


revision = "20260805_61"
down_revision = "20260805_60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("skill_catalog_versions"):
        return
    op.create_table(
        "skill_catalog_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("catalog_version", sa.String(length=64), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("change_summary", sa.JSON(), nullable=False),
        sa.Column("published_by", sa.String(length=36), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["published_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_skill_catalog_versions_version_number",
        "skill_catalog_versions",
        ["version_number"],
        unique=True,
    )
    op.create_index(
        "ix_skill_catalog_versions_catalog_version",
        "skill_catalog_versions",
        ["catalog_version"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_skill_catalog_versions_catalog_version",
        table_name="skill_catalog_versions",
    )
    op.drop_index(
        "ix_skill_catalog_versions_version_number",
        table_name="skill_catalog_versions",
    )
    op.drop_table("skill_catalog_versions")
