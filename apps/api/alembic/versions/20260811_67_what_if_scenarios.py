"""Persist counterfactual matching scenarios for the InsightCard review chain.

Revision ID: 20260811_67
Revises: 20260809_66
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260811_67"
down_revision: str | None = "20260809_66"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("what_if_scenarios"):
        return
    op.create_table(
        "what_if_scenarios",
        sa.Column("scenario_id", sa.String(length=80), primary_key=True),
        sa.Column("evaluation_id", sa.String(length=200), nullable=False),
        sa.Column("actions_payload", sa.JSON(), nullable=False),
        sa.Column("result_payload", sa.JSON(), nullable=False),
        sa.Column("release_id", sa.String(length=128), nullable=True),
        sa.Column("graph_version", sa.String(length=255), nullable=True),
        sa.Column("algorithm_version", sa.String(length=255), nullable=True),
        sa.Column("config_version", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_what_if_scenarios_evaluation_id",
        "what_if_scenarios",
        ["evaluation_id"],
    )
    op.create_index(
        "ix_what_if_scenarios_release_id",
        "what_if_scenarios",
        ["release_id"],
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("what_if_scenarios"):
        return
    op.drop_index(
        "ix_what_if_scenarios_release_id", table_name="what_if_scenarios"
    )
    op.drop_index(
        "ix_what_if_scenarios_evaluation_id", table_name="what_if_scenarios"
    )
    op.drop_table("what_if_scenarios")
