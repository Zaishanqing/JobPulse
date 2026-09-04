"""add stable catalog code to standard skills

Revision ID: 20260728_36
Revises: 20260728_35
"""

import sqlalchemy as sa
from alembic import op


revision = "20260728_36"
down_revision = "20260728_35"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "skills",
        sa.Column("catalog_code", sa.String(length=80), nullable=True),
    )
    op.create_index(
        "ix_skills_catalog_code",
        "skills",
        ["catalog_code"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_skills_catalog_code", table_name="skills")
    op.drop_column("skills", "catalog_code")
