"""link trend reports to graph versions

Revision ID: 20260712_09
Revises: 20260712_08
Create Date: 2026-07-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260712_09"
down_revision: Union[str, Sequence[str], None] = "20260712_08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("trend_reports")
    }
    if "graph_version_id" in columns:
        return
    with op.batch_alter_table("trend_reports") as batch_op:
        batch_op.add_column(sa.Column("graph_version_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_trend_reports_graph_version_id_graph_versions",
            "graph_versions",
            ["graph_version_id"],
            ["id"],
        )
        batch_op.create_index(
            op.f("ix_trend_reports_graph_version_id"), ["graph_version_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("trend_reports") as batch_op:
        batch_op.drop_index(op.f("ix_trend_reports_graph_version_id"))
        batch_op.drop_constraint(
            "fk_trend_reports_graph_version_id_graph_versions", type_="foreignkey"
        )
        batch_op.drop_column("graph_version_id")
