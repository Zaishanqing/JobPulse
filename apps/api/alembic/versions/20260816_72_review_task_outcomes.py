"""Add review_task_outcomes table.

Revision ID: 20260816_72
Revises: 20260813_71
Create Date: 2026-08-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260816_72"
down_revision = "20260813_71"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_task_outcomes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("terminal_event_id", sa.String(length=36), nullable=True),
        sa.Column("outcome_version", sa.String(length=32), nullable=False),
        sa.Column("pre_state_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("post_state_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("blocking_released", sa.Boolean(), nullable=True),
        sa.Column("correction_kind", sa.String(length=32), nullable=True),
        sa.Column("downstream_score_delta", sa.Float(), nullable=True),
        sa.Column("downstream_effect_direction", sa.String(length=16), nullable=True),
        sa.Column("downstream_effect_magnitude", sa.Float(), nullable=True),
        sa.Column("reuse_count_after_review", sa.Integer(), nullable=True),
        sa.Column("observed_fields", sa.JSON(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["review_tasks.id"]),
        sa.ForeignKeyConstraint(
            ["terminal_event_id"], ["review_task_events.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_review_task_outcomes_task_id"),
        "review_task_outcomes",
        ["task_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_review_task_outcomes_task_id"),
        table_name="review_task_outcomes",
    )
    op.drop_table("review_task_outcomes")
