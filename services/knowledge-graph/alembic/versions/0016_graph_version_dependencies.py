"""persist immutable graph version dependency coordinates

Revision ID: 0016_graph_version_dependencies
Revises: 0015_relation_insights
"""

import sqlalchemy as sa
from alembic import op


revision = "0016_graph_version_dependencies"
down_revision = "0015_relation_insights"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("graph_versions") as batch:
        batch.add_column(sa.Column("published_fact_versions", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("skill_catalog_version", sa.String(128), nullable=False, server_default="legacy-unspecified"))
        batch.add_column(sa.Column("mapping_snapshot_version", sa.String(128), nullable=False, server_default="legacy-unspecified"))
        batch.add_column(sa.Column("normalization_algorithm_version", sa.String(128), nullable=False, server_default="legacy-unspecified"))
        batch.add_column(sa.Column("build_config_version", sa.String(128), nullable=False, server_default="legacy-unspecified"))
        batch.add_column(sa.Column("source_time_window", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("graph_versions") as batch:
        batch.drop_column("source_time_window")
        batch.drop_column("build_config_version")
        batch.drop_column("normalization_algorithm_version")
        batch.drop_column("mapping_snapshot_version")
        batch.drop_column("skill_catalog_version")
        batch.drop_column("published_fact_versions")
