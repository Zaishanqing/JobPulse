"""store enterprise JD requirement graph

Revision ID: 20260809_63
Revises: 1ce986a44e26
"""

from alembic import op
import sqlalchemy as sa


revision = "20260809_63"
down_revision = "1ce986a44e26"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("enterprise_jobs")
    }
    if "requirement_graph" not in existing:
        op.add_column(
            "enterprise_jobs",
            sa.Column("requirement_graph", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    existing = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("enterprise_jobs")
    }
    if "requirement_graph" in existing:
        op.drop_column("enterprise_jobs", "requirement_graph")
