"""Drop legacy main-system graph_versions table.

Formal position graphs and versions are owned by the Knowledge Graph
service; the main system no longer maintains a local graph version table.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260803_52"
down_revision = "20260803_51"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "graph_versions" in op.get_bind().dialect.get_table_names(op.get_bind()):
        op.drop_table("graph_versions")


def downgrade() -> None:
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
