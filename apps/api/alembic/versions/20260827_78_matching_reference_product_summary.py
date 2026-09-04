"""Add product-level matching summary to personal matching history.

Revision ID: 20260827_78
Revises: 20260819_77

The existing matching_service_references table is the durable history for
personal matching runs.  This migration only adds nullable product summary
columns (matching method, degraded flag and final overall score) so the
existing history list can show mature information without replacing the table
or creating a second history system.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260827_78"
down_revision = "20260819_77"
branch_labels = None
depends_on = None


def _column_names(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    columns = _column_names("matching_service_references")
    if "matching_method" not in columns:
        op.add_column(
            "matching_service_references",
            sa.Column("matching_method", sa.String(32), nullable=True),
        )
    if "degraded" not in columns:
        op.add_column(
            "matching_service_references",
            sa.Column("degraded", sa.Boolean(), nullable=True),
        )
    if "overall_score" not in columns:
        op.add_column(
            "matching_service_references",
            sa.Column("overall_score", sa.Float(), nullable=True),
        )


def downgrade() -> None:
    columns = _column_names("matching_service_references")
    if "overall_score" in columns:
        op.drop_column("matching_service_references", "overall_score")
    if "degraded" in columns:
        op.drop_column("matching_service_references", "degraded")
    if "matching_method" in columns:
        op.drop_column("matching_service_references", "matching_method")
