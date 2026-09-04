"""add transactional task outbox

Revision ID: 20260727_0003
Revises: 20260727_0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260727_0003"
down_revision = "20260727_0002"
branch_labels = None
depends_on = None

contract_json = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "outbox_records",
        sa.Column("outbox_id", sa.String(length=64), primary_key=True),
        sa.Column("access_scope", sa.String(length=200), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("message_id", sa.String(length=64), nullable=False),
        sa.Column("payload", contract_json, nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_by", sa.String(length=200)),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(length=200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["access_scope", "task_id"],
            ["evaluation_tasks.access_scope", "evaluation_tasks.task_id"],
            name="fk_outbox_records_task",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("access_scope", "task_id", name="uq_outbox_records_task"),
        sa.UniqueConstraint("message_id", name="uq_outbox_records_message"),
        sa.CheckConstraint(
            "status IN ('pending', 'claimed', 'published')",
            name="ck_outbox_records_status",
        ),
        sa.CheckConstraint("attempt >= 0", name="ck_outbox_records_attempt"),
    )
    op.create_index(
        "ix_outbox_records_dispatch",
        "outbox_records",
        ["status", "available_at", "claim_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_records_dispatch", table_name="outbox_records")
    op.drop_table("outbox_records")
