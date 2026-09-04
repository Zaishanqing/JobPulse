"""Persist v2 lifecycle transition details.

Lifecycle v2 stores enough trajectory evidence to replay every transition.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0003_candidate_lifecycle_v2"
down_revision = "0002_candidate_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candidate_status_transitions",
        sa.Column(
            "details",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("candidate_status_transitions", "details")
