"""Persist complete per-skill trend details.

Revision ID: 20260731_43
Revises: 20260730_42
"""

from alembic import op
import sqlalchemy as sa


revision = "20260731_43"
down_revision = "20260730_42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {item["name"] for item in inspector.get_columns("trend_reports")}
    if "skill_trend_details" not in existing:
        with op.batch_alter_table("trend_reports") as batch:
            batch.add_column(
                sa.Column(
                    "skill_trend_details",
                    sa.JSON(),
                    nullable=False,
                    server_default="[]",
                )
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {item["name"] for item in inspector.get_columns("trend_reports")}
    if "skill_trend_details" in existing:
        with op.batch_alter_table("trend_reports") as batch:
            batch.drop_column("skill_trend_details")
