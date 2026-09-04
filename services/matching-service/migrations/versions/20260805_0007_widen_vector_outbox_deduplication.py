"""widen vector outbox deduplication key

Revision ID: 20260805_0007
Revises: 20260805_0006
"""

import sqlalchemy as sa
from alembic import op

revision = "20260805_0007"
down_revision = "20260805_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("vector_outbox_events") as batch:
        batch.alter_column(
            "deduplication_key",
            existing_type=sa.String(length=64),
            type_=sa.String(length=700),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("vector_outbox_events") as batch:
        batch.alter_column(
            "deduplication_key",
            existing_type=sa.String(length=700),
            type_=sa.String(length=64),
            existing_nullable=False,
        )
