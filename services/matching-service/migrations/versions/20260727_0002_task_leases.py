"""Add atomic worker lease ownership.

Revision ID: 20260727_0002
Revises: 20260727_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260727_0002"
down_revision: str | None = "20260727_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("evaluation_tasks", sa.Column("lease_owner", sa.String(200)))
    op.add_column(
        "evaluation_tasks", sa.Column("lease_expires_at", sa.DateTime(timezone=True))
    )
    op.create_index(
        "ix_evaluation_tasks_claim",
        "evaluation_tasks",
        ["status", "lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_evaluation_tasks_claim", table_name="evaluation_tasks")
    op.drop_column("evaluation_tasks", "lease_expires_at")
    op.drop_column("evaluation_tasks", "lease_owner")
