"""Persist immutable offline bundle identity digest.

Revision ID: 20260818_75
Revises: 20260817_74
"""

from alembic import op
import sqlalchemy as sa


revision = "20260818_75"
down_revision = "20260817_74"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("offline_import_batches")}
    if "bundle_digest" not in columns:
        op.add_column(
            "offline_import_batches",
            sa.Column("bundle_digest", sa.String(64), nullable=True),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("offline_import_batches")}
    if "bundle_digest" in columns:
        op.drop_column("offline_import_batches", "bundle_digest")
