"""Store auditable Trend report review adjustments.

Revision ID: 20260804_53
Revises: 20260803_52
"""

from alembic import op
import sqlalchemy as sa


revision = "20260804_53"
down_revision = "20260803_52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trend_report_review_adjustments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("report_id", sa.String(length=36), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("before_values", sa.JSON(), nullable=False),
        sa.Column("after_values", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["report_id"], ["trend_reports.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_trend_report_review_adjustments_report_id"),
        "trend_report_review_adjustments", ["report_id"],
    )
    op.create_index(
        op.f("ix_trend_report_review_adjustments_actor_user_id"),
        "trend_report_review_adjustments", ["actor_user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_trend_report_review_adjustments_actor_user_id"),
        table_name="trend_report_review_adjustments",
    )
    op.drop_index(
        op.f("ix_trend_report_review_adjustments_report_id"),
        table_name="trend_report_review_adjustments",
    )
    op.drop_table("trend_report_review_adjustments")
