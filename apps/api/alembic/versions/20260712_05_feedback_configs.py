"""persist feedback and system configuration

Revision ID: 20260712_05
Revises: 20260712_04
Create Date: 2026-07-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260712_05"
down_revision: Union[str, Sequence[str], None] = "20260712_04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if {"feedback_records", "system_configs"} <= existing_tables:
        return
    op.create_table(
        "feedback_records",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("feedback_type", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_feedback_records_created_by"), "feedback_records", ["created_by"])
    op.create_index(op.f("ix_feedback_records_feedback_type"), "feedback_records", ["feedback_type"])
    op.create_index(op.f("ix_feedback_records_status"), "feedback_records", ["status"])
    op.create_table(
        "system_configs",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(length=36), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("name"),
    )


def downgrade() -> None:
    op.drop_table("system_configs")
    op.drop_index(op.f("ix_feedback_records_status"), table_name="feedback_records")
    op.drop_index(op.f("ix_feedback_records_feedback_type"), table_name="feedback_records")
    op.drop_index(op.f("ix_feedback_records_created_by"), table_name="feedback_records")
    op.drop_table("feedback_records")
