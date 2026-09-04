"""Persist main-system learning path history.

Revision ID: 20260813_71
Revises: 20260812_70
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260813_71"
down_revision: str | None = "20260812_70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "learning_path_records",
        sa.Column("path_id", sa.String(length=80), nullable=False),
        sa.Column("evaluation_id", sa.String(length=200), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("target_position_id", sa.String(length=64), nullable=True),
        sa.Column("time_budget_hours", sa.Float(), nullable=True),
        sa.Column("gap_analysis", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("algorithm_versions", sa.JSON(), nullable=False),
        sa.Column("data_versions", sa.JSON(), nullable=False),
        sa.Column("versions", sa.JSON(), nullable=False),
        sa.Column("resume_id", sa.String(length=36), nullable=True),
        sa.Column("validated_cv_snapshot_id", sa.String(length=36), nullable=True),
        sa.Column("position_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["evaluation_id"],
            ["matching_service_references.evaluation_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("path_id"),
    )
    op.create_index("ix_learning_path_records_user_id", "learning_path_records", ["user_id"])
    op.create_index(
        "ix_learning_path_records_target_position_id",
        "learning_path_records",
        ["target_position_id"],
    )
    op.create_index(
        "ix_learning_path_owner_created",
        "learning_path_records",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_learning_path_evaluation_created",
        "learning_path_records",
        ["evaluation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("learning_path_records")
