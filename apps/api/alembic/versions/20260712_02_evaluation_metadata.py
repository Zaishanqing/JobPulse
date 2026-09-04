"""add truthful evaluation metadata

Revision ID: 20260712_02
Revises: 20260712_01
Create Date: 2026-07-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260712_02"
down_revision: Union[str, Sequence[str], None] = "20260712_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    existing_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("evaluation_reports")
    }
    required_columns = {
        "evaluation_status",
        "algorithm_version",
        "config_snapshot",
        "evaluated_count",
        "error_count",
    }
    if required_columns <= existing_columns:
        return
    with op.batch_alter_table("evaluation_reports") as batch_op:
        batch_op.add_column(sa.Column("evaluation_status", sa.String(length=32), nullable=False, server_default="insufficient_data"))
        batch_op.add_column(sa.Column("algorithm_version", sa.String(length=128), nullable=False, server_default="rule-eval-v1"))
        batch_op.add_column(sa.Column("config_snapshot", sa.JSON(), nullable=False, server_default="{}"))
        batch_op.add_column(sa.Column("evaluated_count", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"))
        batch_op.create_check_constraint(
            "ck_evaluation_reports_status_allowed",
            "evaluation_status in ('completed', 'insufficient_data')",
        )


def downgrade() -> None:
    with op.batch_alter_table("evaluation_reports") as batch_op:
        batch_op.drop_constraint("ck_evaluation_reports_status_allowed", type_="check")
        batch_op.drop_column("error_count")
        batch_op.drop_column("evaluated_count")
        batch_op.drop_column("config_snapshot")
        batch_op.drop_column("algorithm_version")
        batch_op.drop_column("evaluation_status")
