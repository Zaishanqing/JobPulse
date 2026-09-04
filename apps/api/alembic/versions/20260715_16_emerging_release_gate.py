"""Persist discovery run status used by the main-system release gate."""
from alembic import op
import sqlalchemy as sa

revision = "20260715_16"
down_revision = "20260715_15"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("position_clusters", sa.Column("discovery_run_status", sa.String(32)))


def downgrade():
    op.drop_column("position_clusters", "discovery_run_status")
