"""add extraction task worker leases

Revision ID: 20260723_24
Revises: 20260723_23
"""

from alembic import op
import sqlalchemy as sa


revision = "20260723_24"
down_revision = "20260723_23"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("extraction_tasks")}
    indexes = {item["name"] for item in inspector.get_indexes("extraction_tasks")}
    with op.batch_alter_table("extraction_tasks") as batch:
        if "claimed_by" not in columns:
            batch.add_column(sa.Column("claimed_by", sa.String(length=120), nullable=True))
        if "lease_expires_at" not in columns:
            batch.add_column(
                sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
            )
        if "heartbeat_at" not in columns:
            batch.add_column(
                sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True)
            )
    if "ix_extraction_tasks_claimed_by" not in indexes:
        op.create_index(
            "ix_extraction_tasks_claimed_by", "extraction_tasks", ["claimed_by"]
        )
    if "ix_extraction_tasks_lease_expires_at" not in indexes:
        op.create_index(
            "ix_extraction_tasks_lease_expires_at",
            "extraction_tasks",
            ["lease_expires_at"],
        )


def downgrade() -> None:
    op.drop_index("ix_extraction_tasks_lease_expires_at", table_name="extraction_tasks")
    op.drop_index("ix_extraction_tasks_claimed_by", table_name="extraction_tasks")
    with op.batch_alter_table("extraction_tasks") as batch:
        batch.drop_column("heartbeat_at")
        batch.drop_column("lease_expires_at")
        batch.drop_column("claimed_by")
