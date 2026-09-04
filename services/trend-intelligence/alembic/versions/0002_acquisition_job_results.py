"""Add acquisition crawl job result counters.

Revision ID: 0002_acquisition_job_results
Revises: 0001_postgresql_baseline
"""

import sqlalchemy as sa
from alembic import op


revision = "0002_acquisition_job_results"
down_revision = "0001_postgresql_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("crawl_jobs")}
    for name in ("fetched_count", "new_snapshot_count", "duplicate_count"):
        if name not in existing:
            op.add_column(
                "crawl_jobs",
                sa.Column(name, sa.Integer(), nullable=False, server_default="0"),
            )


def downgrade() -> None:
    op.drop_column("crawl_jobs", "duplicate_count")
    op.drop_column("crawl_jobs", "new_snapshot_count")
    op.drop_column("crawl_jobs", "fetched_count")
