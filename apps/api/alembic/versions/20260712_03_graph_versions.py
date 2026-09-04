"""persist graph versions

Revision ID: 20260712_03
Revises: 20260712_02
Create Date: 2026-07-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260712_03"
down_revision: Union[str, Sequence[str], None] = "20260712_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "graph_versions" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "graph_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("position_id", sa.String(length=36), nullable=False),
        sa.Column("version_name", sa.String(length=255), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("rollback_of_version_id", sa.String(length=36), nullable=True),
        sa.Column("is_rollback", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["position_id"], ["standard_positions.id"]),
        sa.ForeignKeyConstraint(["rollback_of_version_id"], ["graph_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_graph_versions_created_by"), "graph_versions", ["created_by"])
    op.create_index(op.f("ix_graph_versions_position_id"), "graph_versions", ["position_id"])
    op.create_index(
        op.f("ix_graph_versions_rollback_of_version_id"),
        "graph_versions",
        ["rollback_of_version_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_graph_versions_rollback_of_version_id"), table_name="graph_versions")
    op.drop_index(op.f("ix_graph_versions_position_id"), table_name="graph_versions")
    op.drop_index(op.f("ix_graph_versions_created_by"), table_name="graph_versions")
    op.drop_table("graph_versions")
