"""Drop unused crawl job schedule.

Revision ID: 0005_drop_crawl_job_schedule
Revises: 0004_trend_input_staging
"""

import sqlalchemy as sa
from alembic import op


revision = "0005_drop_crawl_job_schedule"
down_revision = "0004_trend_input_staging"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("crawl_jobs")}
    if "schedule" in columns:
        op.drop_column("crawl_jobs", "schedule")


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("crawl_jobs")}
    if "schedule" not in columns:
        op.add_column(
            "crawl_jobs",
            sa.Column("schedule", sa.JSON(), nullable=False, server_default="{}"),
        )
