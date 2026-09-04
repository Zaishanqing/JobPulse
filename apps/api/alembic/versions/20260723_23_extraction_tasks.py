"""add durable extraction tasks

Revision ID: 20260723_23
Revises: 20260723_22
"""

from alembic import op
import sqlalchemy as sa


revision = "20260723_23"
down_revision = "20260723_22"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("extraction_tasks"):
        base_required = {
            "id",
            "source_jd_version_id",
            "status",
            "provider",
            "attempt_count",
            "max_attempts",
            "started_at",
            "finished_at",
            "last_error_code",
            "last_error_message",
            "retryable",
            "bundle_payload",
            "created_at",
            "updated_at",
        }
        columns = {item["name"] for item in inspector.get_columns("extraction_tasks")}
        if "request_fingerprint" in columns:
            required = base_required | {"request_fingerprint"}
            idempotency_unique = ("source_jd_version_id", "request_fingerprint")
        elif "request_id" in columns:
            required = base_required | {"request_id"}
            idempotency_unique = ("source_jd_version_id", "request_id")
        else:
            raise RuntimeError(
                "Existing extraction_tasks table lacks a request identity column"
            )
        missing = required - columns
        if missing:
            raise RuntimeError(
                f"Existing extraction_tasks table is incomplete; missing: {sorted(missing)}"
            )
        uniques = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints("extraction_tasks")
        }
        if idempotency_unique not in uniques:
            raise RuntimeError("Existing extraction_tasks table lacks idempotency uniqueness")
        return
    op.create_table(
        "extraction_tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_jd_version_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("bundle_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status in ('pending', 'running', 'succeeded', 'failed')",
            name="ck_extraction_tasks_status_allowed",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_extraction_tasks_attempt_nonnegative"
        ),
        sa.CheckConstraint(
            "max_attempts > 0", name="ck_extraction_tasks_max_attempts_positive"
        ),
        sa.CheckConstraint(
            "attempt_count <= max_attempts",
            name="ck_extraction_tasks_attempt_within_max",
        ),
        sa.ForeignKeyConstraint(
            ["source_jd_version_id"], ["source_jd_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_jd_version_id",
            "request_fingerprint",
            name="uq_extraction_tasks_version_fingerprint",
        ),
    )
    op.create_index(
        "ix_extraction_tasks_source_jd_version_id",
        "extraction_tasks",
        ["source_jd_version_id"],
    )
    op.create_index("ix_extraction_tasks_status", "extraction_tasks", ["status"])


def downgrade() -> None:
    op.drop_index("ix_extraction_tasks_status", table_name="extraction_tasks")
    op.drop_index(
        "ix_extraction_tasks_source_jd_version_id", table_name="extraction_tasks"
    )
    op.drop_table("extraction_tasks")
