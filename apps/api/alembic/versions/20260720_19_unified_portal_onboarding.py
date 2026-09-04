"""track standard-position graph onboarding

Revision ID: 20260720_19
Revises: 20260719_18
"""

from alembic import op
import sqlalchemy as sa


revision = "20260720_19"
down_revision = "20260719_18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("standard_positions") as batch:
        batch.add_column(
            sa.Column(
                "graph_onboarding_status",
                sa.String(length=32),
                nullable=False,
                server_default="mapping_required",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("standard_positions") as batch:
        batch.drop_column("graph_onboarding_status")
