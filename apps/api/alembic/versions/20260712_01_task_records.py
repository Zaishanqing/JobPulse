"""add persistent task records

Revision ID: 20260712_01
Revises: f3819d64bc82
Create Date: 2026-07-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260712_01"
down_revision: Union[str, Sequence[str], None] = "f3819d64bc82"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "task_records" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "task_records",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("task_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("input_payload", sa.JSON(), nullable=False),
        sa.Column("result_payload", sa.JSON(), nullable=False),
        sa.Column("result_reference", sa.String(length=255), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("log_entries", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "progress >= 0 and progress <= 1",
            name="ck_task_records_progress_range",
        ),
        sa.CheckConstraint(
            "status in ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_task_records_status_allowed",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_records_created_by", "task_records", ["created_by"])
    op.create_index("ix_task_records_status", "task_records", ["status"])
    op.create_index("ix_task_records_task_type", "task_records", ["task_type"])


def downgrade() -> None:
    op.drop_index("ix_task_records_task_type", table_name="task_records")
    op.drop_index("ix_task_records_status", table_name="task_records")
    op.drop_index("ix_task_records_created_by", table_name="task_records")
    op.drop_table("task_records")
