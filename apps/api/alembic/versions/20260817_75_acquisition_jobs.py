"""Add acquisition jobs table.

Revision ID: 20260817_75
Revises: 20260817_74
Create Date: 2026-08-17
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260817_75"
down_revision = "20260817_74"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Historical application startup could have materialized this table with
    # ``Base.metadata.create_all`` before Alembic reached this revision.
    if sa.inspect(op.get_bind()).has_table("acquisition_jobs"):
        return
    op.create_table(
        "acquisition_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("requested_by", sa.String(length=36), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("keyword", sa.String(length=255), nullable=False),
        sa.Column("city", sa.String(length=64), nullable=False),
        sa.Column("pages", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("crawler_task_id", sa.String(length=64), nullable=True),
        sa.Column("bundle_id", sa.String(length=128), nullable=True),
        sa.Column("bundle_file_name", sa.String(length=255), nullable=True),
        sa.Column("bundle_hash", sa.String(length=128), nullable=True),
        sa.Column("discovered_count", sa.Integer(), nullable=False),
        sa.Column("exported_count", sa.Integer(), nullable=False),
        sa.Column("imported_count", sa.Integer(), nullable=False),
        sa.Column("no_op_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("import_batch_id", sa.String(length=36), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_of_id", sa.String(length=36), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('pending', 'crawling', 'exporting', 'verifying', "
            "'importing', 'completed', 'crawl_failed', 'export_failed', "
            "'verify_failed', 'import_failed', 'cancelled')",
            name="ck_acquisition_jobs_status_allowed",
        ),
        sa.CheckConstraint(
            "pages > 0", name="ck_acquisition_jobs_pages_positive"
        ),
        sa.CheckConstraint(
            "progress >= 0 AND progress <= 1",
            name="ck_acquisition_jobs_progress_range",
        ),
        sa.CheckConstraint(
            "attempt >= 1", name="ck_acquisition_jobs_attempt_positive"
        ),
        sa.CheckConstraint(
            "discovered_count >= 0 AND exported_count >= 0 "
            "AND imported_count >= 0 AND no_op_count >= 0 AND failed_count >= 0",
            name="ck_acquisition_jobs_counts_nonnegative",
        ),
        sa.CheckConstraint(
            "imported_count + no_op_count + failed_count <= exported_count",
            name="ck_acquisition_jobs_counts_total",
        ),
    )
    op.create_index(
        op.f("ix_acquisition_jobs_source"),
        "acquisition_jobs",
        ["source"],
        unique=False,
    )
    op.create_index(
        op.f("ix_acquisition_jobs_status"),
        "acquisition_jobs",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_acquisition_jobs_bundle_id"),
        "acquisition_jobs",
        ["bundle_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_acquisition_jobs_retry_of_id"),
        "acquisition_jobs",
        ["retry_of_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_acquisition_jobs_retry_of_id"), table_name="acquisition_jobs")
    op.drop_index(op.f("ix_acquisition_jobs_bundle_id"), table_name="acquisition_jobs")
    op.drop_index(op.f("ix_acquisition_jobs_status"), table_name="acquisition_jobs")
    op.drop_index(op.f("ix_acquisition_jobs_source"), table_name="acquisition_jobs")
    op.drop_table("acquisition_jobs")
