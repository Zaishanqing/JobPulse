"""add local SQLite offline bundle import tracking

Revision ID: 20260726_32
Revises: 20260726_31
"""

import sqlalchemy as sa
from alembic import op


revision = "20260726_32"
down_revision = "20260726_31"
branch_labels = None
depends_on = None


def _create_batches_table() -> None:
    op.create_table(
        "offline_import_batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("bundle_id", sa.String(128), nullable=False),
        sa.Column("bundle_schema_version", sa.String(64), nullable=False),
        sa.Column("record_schema_version", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("parent_bundle_id", sa.String(128), nullable=True),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("imported_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.UniqueConstraint("bundle_id", name="uq_offline_import_batches_bundle_id"),
        sa.CheckConstraint(
            "mode IN ('incremental', 'full')",
            name="ck_offline_import_batches_mode",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'importing', 'completed', 'completed_with_errors', 'failed')",
            name="ck_offline_import_batches_status",
        ),
        sa.CheckConstraint(
            "record_count >= 0 AND imported_count >= 0 "
            "AND skipped_count >= 0 AND failed_count >= 0",
            name="ck_offline_import_batches_counts_nonnegative",
        ),
        sa.CheckConstraint(
            "imported_count + skipped_count + failed_count <= record_count",
            name="ck_offline_import_batches_counts_total",
        ),
    )
    op.create_index(
        "ix_offline_import_batches_parent_bundle_id",
        "offline_import_batches",
        ["parent_bundle_id"],
    )


def _create_items_table() -> None:
    op.create_table(
        "offline_import_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_id", sa.String(36), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("source_platform", sa.String(64), nullable=True),
        sa.Column("source_record_id", sa.String(255), nullable=True),
        sa.Column("source_version", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("source_jd_id", sa.String(36), nullable=True),
        sa.Column("source_jd_version_id", sa.String(36), nullable=True),
        sa.Column("extraction_task_id", sa.String(36), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["offline_import_batches.id"],
            ondelete="RESTRICT",
            name="fk_offline_import_items_batch_id",
        ),
        sa.UniqueConstraint(
            "batch_id",
            "line_number",
            name="uq_offline_import_items_batch_line",
        ),
        sa.CheckConstraint("line_number > 0", name="ck_offline_import_items_line_number"),
        sa.CheckConstraint(
            "status IN ('pending', 'imported', 'skipped', 'failed')",
            name="ck_offline_import_items_status",
        ),
    )
    op.create_index(
        "ix_offline_import_items_batch_id",
        "offline_import_items",
        ["batch_id"],
    )


def upgrade() -> None:
    # Historical application startup called Base.metadata.create_all(), so a
    # revision-31 database can already contain one or both revision-32 tables.
    # Adopt those matching ORM tables without attempting duplicate DDL.
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("offline_import_batches"):
        _create_batches_table()
    if not inspector.has_table("offline_import_items"):
        _create_items_table()


def downgrade() -> None:
    op.drop_index("ix_offline_import_items_batch_id", table_name="offline_import_items")
    op.drop_table("offline_import_items")
    op.drop_index(
        "ix_offline_import_batches_parent_bundle_id",
        table_name="offline_import_batches",
    )
    op.drop_table("offline_import_batches")
