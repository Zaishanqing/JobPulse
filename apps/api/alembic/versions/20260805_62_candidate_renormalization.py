"""record candidate renormalization catalog version

Revision ID: 20260805_62
Revises: 20260805_61
"""

from alembic import op
import sqlalchemy as sa


revision = "20260805_62"
down_revision = "20260805_61"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(
            "skill_normalization_candidates"
        )
    }
    if "normalization_catalog_version" in existing_columns:
        return
    op.add_column(
        "skill_normalization_candidates",
        sa.Column("normalization_catalog_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "skill_normalization_candidates",
        sa.Column("normalized_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("skill_normalization_candidates", "normalized_at")
    op.drop_column(
        "skill_normalization_candidates", "normalization_catalog_version"
    )
