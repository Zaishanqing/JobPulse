"""Store the complete legacy JD experience summary.

Revision ID: 20260802_47
Revises: 20260801_46
"""

from alembic import op
import sqlalchemy as sa


revision = "20260802_47"
down_revision = "20260801_46"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("jd_parse_results") as batch_op:
        batch_op.alter_column(
            "experience",
            existing_type=sa.String(length=64),
            type_=sa.Text(),
            existing_nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("jd_parse_results") as batch_op:
        batch_op.alter_column(
            "experience",
            existing_type=sa.Text(),
            type_=sa.String(length=64),
            existing_nullable=True,
        )
