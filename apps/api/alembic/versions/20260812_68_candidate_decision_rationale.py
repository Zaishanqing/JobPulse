"""Persist recruiter rationale on formal candidate decisions.

Revision ID: 20260812_68
Revises: 20260811_67
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260812_68"
down_revision: str | None = "20260811_67"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {
        column["name"] for column in inspector.get_columns("candidate_decisions")
    }
    if "reason_code" not in columns:
        op.add_column(
            "candidate_decisions",
            sa.Column("reason_code", sa.String(length=64), nullable=True),
        )
    if "reason_text" not in columns:
        op.add_column(
            "candidate_decisions",
            sa.Column("reason_text", sa.String(length=2000), nullable=True),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {
        column["name"] for column in inspector.get_columns("candidate_decisions")
    }
    if "reason_text" in columns:
        op.drop_column("candidate_decisions", "reason_text")
    if "reason_code" in columns:
        op.drop_column("candidate_decisions", "reason_code")
