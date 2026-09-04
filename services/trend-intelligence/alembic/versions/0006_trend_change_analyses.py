"""Add Trend Change analysis storage.

Revision ID: 0006_trend_change_analyses
Revises: 0005_drop_crawl_job_schedule
"""

import sqlalchemy as sa
from alembic import op


revision = "0006_trend_change_analyses"
down_revision = "0005_drop_crawl_job_schedule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "trend_change_analyses" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "trend_change_analyses",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("algorithm_version", sa.String(length=128), nullable=False),
            sa.Column("config_version", sa.String(length=128), nullable=False),
            sa.Column("result_payload", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    if "trend_change_analyses" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("trend_change_analyses")
