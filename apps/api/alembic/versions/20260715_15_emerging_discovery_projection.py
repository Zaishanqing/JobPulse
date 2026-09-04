"""Add emerging discovery projection metadata.

Revision ID: 20260715_15
Revises: 20260713_14
"""
from alembic import op
import sqlalchemy as sa

revision = "20260715_15"
down_revision = "20260713_14"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("position_clusters") as batch:
        batch.add_column(sa.Column("discovery_run_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("discovery_assessment", sa.JSON(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("generated_definition", sa.JSON(), nullable=False, server_default="{}"))
        batch.create_index("ix_position_clusters_discovery_run_id", ["discovery_run_id"])


def downgrade():
    with op.batch_alter_table("position_clusters") as batch:
        batch.drop_index("ix_position_clusters_discovery_run_id")
        batch.drop_column("generated_definition")
        batch.drop_column("discovery_assessment")
        batch.drop_column("discovery_run_id")
