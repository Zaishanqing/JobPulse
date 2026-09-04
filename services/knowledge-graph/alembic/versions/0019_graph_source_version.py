"""persist the explicit source version for published graph versions

Revision ID: 0019_graph_source_version
Revises: 0018_demo_dataset_isolation
"""

from alembic import op
import sqlalchemy as sa

revision = "0019_graph_source_version"
down_revision = "0018_demo_dataset_isolation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("graph_versions") as batch:
        batch.add_column(sa.Column("source_version", sa.String(64), nullable=False, server_default="legacy-graph-source-v1"))
        batch.drop_column("content_hash")
    if op.get_bind().dialect.name == "sqlite":
        op.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_graph_versions_reject_update
            BEFORE UPDATE ON graph_versions
            BEGIN
                SELECT RAISE(ABORT, 'graph_versions are immutable');
            END
        """)
        op.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_graph_versions_reject_delete
            BEFORE DELETE ON graph_versions
            BEGIN
                SELECT RAISE(ABORT, 'graph_versions are immutable');
            END
        """)


def downgrade() -> None:
    with op.batch_alter_table("graph_versions") as batch:
        batch.drop_column("source_version")
