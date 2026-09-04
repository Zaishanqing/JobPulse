"""Add real Trend input staging for Acquisition bundles.

Revision ID: 0004_trend_input_staging
Revises: 0003_snapshot_lineage_bundles
"""

import sqlalchemy as sa
from alembic import op


revision = "0004_trend_input_staging"
down_revision = "0003_snapshot_lineage_bundles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    bundle_columns = {
        column["name"] for column in inspector.get_columns("acquisition_bundles")
    }
    if "analysis_run_id" not in bundle_columns:
        op.add_column(
            "acquisition_bundles",
            sa.Column("analysis_run_id", sa.String(length=36), nullable=True),
        )
        op.create_foreign_key(
            "fk_acquisition_bundles_analysis_run_id",
            "acquisition_bundles",
            "analysis_runs",
            ["analysis_run_id"],
            ["id"],
        )
        op.create_index(
            "ix_acquisition_bundles_analysis_run_id",
            "acquisition_bundles",
            ["analysis_run_id"],
        )

    if "trend_input_records" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "trend_input_records",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("bundle_id", sa.String(length=36), nullable=False),
            sa.Column("acquisition_snapshot_id", sa.String(length=36), nullable=False),
            sa.Column("analysis_run_id", sa.String(length=36), nullable=False),
            sa.Column("source", sa.String(length=32), nullable=False),
            sa.Column("external_id", sa.String(length=256), nullable=False),
            sa.Column("source_version", sa.String(length=64), nullable=False),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("url", sa.Text(), nullable=False),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("record_metadata", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"]),
            sa.ForeignKeyConstraint(["bundle_id"], ["acquisition_bundles.id"]),
            sa.ForeignKeyConstraint(["acquisition_snapshot_id"], ["raw_snapshots.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "bundle_id",
                "acquisition_snapshot_id",
                name="uq_trend_input_bundle_snapshot",
            ),
        )
        op.create_index(
            "ix_trend_input_records_bundle_id", "trend_input_records", ["bundle_id"],
        )
        op.create_index(
            "ix_trend_input_records_acquisition_snapshot_id",
            "trend_input_records",
            ["acquisition_snapshot_id"],
        )
        op.create_index(
            "ix_trend_input_records_analysis_run_id",
            "trend_input_records",
            ["analysis_run_id"],
        )
        op.create_index(
            "ix_trend_input_records_source", "trend_input_records", ["source"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "trend_input_records" in sa.inspect(bind).get_table_names():
        op.drop_table("trend_input_records")
    columns = {
        column["name"] for column in sa.inspect(bind).get_columns("acquisition_bundles")
    }
    if "analysis_run_id" in columns:
        op.drop_index(
            "ix_acquisition_bundles_analysis_run_id",
            table_name="acquisition_bundles",
        )
        op.drop_constraint(
            "fk_acquisition_bundles_analysis_run_id",
            "acquisition_bundles",
            type_="foreignkey",
        )
        op.drop_column("acquisition_bundles", "analysis_run_id")
