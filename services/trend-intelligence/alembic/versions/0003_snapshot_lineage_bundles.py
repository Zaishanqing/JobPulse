"""Add snapshot observations and complete bundle lineage.

Revision ID: 0003_snapshot_lineage_bundles
Revises: 0002_acquisition_job_results
"""

import sqlalchemy as sa
from alembic import op


revision = "0003_snapshot_lineage_bundles"
down_revision = "0002_acquisition_job_results"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "raw_snapshot_observations" not in inspector.get_table_names():
        op.create_table(
            "raw_snapshot_observations",
            sa.Column("job_id", sa.String(length=36), nullable=False),
            sa.Column("snapshot_id", sa.String(length=36), nullable=False),
            sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["job_id"], ["crawl_jobs.id"]),
            sa.ForeignKeyConstraint(["snapshot_id"], ["raw_snapshots.id"]),
            sa.PrimaryKeyConstraint("job_id", "snapshot_id"),
        )
        op.execute(sa.text(
            """
            INSERT INTO raw_snapshot_observations (job_id, snapshot_id, observed_at)
            SELECT job_id, id, captured_at FROM raw_snapshots
            ON CONFLICT (job_id, snapshot_id) DO NOTHING
            """
        ))

    columns = {
        column["name"] for column in sa.inspect(bind).get_columns("acquisition_bundles")
    }
    if "snapshot_ids" not in columns:
        op.add_column(
            "acquisition_bundles",
            sa.Column("snapshot_ids", sa.JSON(), nullable=True),
        )
        op.execute(sa.text(
            """
            UPDATE acquisition_bundles
            SET snapshot_ids = COALESCE(payload->'snapshot_ids', '[]'::json)
            """
        ))
        op.alter_column("acquisition_bundles", "snapshot_ids", nullable=False)
    if "window_start" not in columns:
        op.add_column(
            "acquisition_bundles",
            sa.Column("window_start", sa.DateTime(timezone=True), nullable=True),
        )
        op.execute(sa.text(
            """
            UPDATE acquisition_bundles AS bundle
            SET window_start = job.window_start
            FROM crawl_jobs AS job
            WHERE job.id = bundle.job_id
            """
        ))
        op.alter_column("acquisition_bundles", "window_start", nullable=False)
    if "window_end" not in columns:
        op.add_column(
            "acquisition_bundles",
            sa.Column("window_end", sa.DateTime(timezone=True), nullable=True),
        )
        op.execute(sa.text(
            """
            UPDATE acquisition_bundles AS bundle
            SET window_end = job.window_end
            FROM crawl_jobs AS job
            WHERE job.id = bundle.job_id
            """
        ))
        op.alter_column("acquisition_bundles", "window_end", nullable=False)


def downgrade() -> None:
    bind = op.get_bind()
    columns = {
        column["name"] for column in sa.inspect(bind).get_columns("acquisition_bundles")
    }
    for name in ("window_end", "window_start", "snapshot_ids"):
        if name in columns:
            op.drop_column("acquisition_bundles", name)
    if "raw_snapshot_observations" in sa.inspect(bind).get_table_names():
        op.drop_table("raw_snapshot_observations")
