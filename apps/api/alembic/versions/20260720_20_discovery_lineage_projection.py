"""project immutable discovery lineage facts

Revision ID: 20260720_20
Revises: 20260720_19
"""

from alembic import op
import sqlalchemy as sa


revision = "20260720_20"
down_revision = "20260720_19"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("position_clusters") as batch:
        batch.add_column(
            sa.Column(
                "discovery_lineages",
                sa.JSON(),
                nullable=False,
                server_default="[]",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("position_clusters") as batch:
        batch.drop_column("discovery_lineages")
