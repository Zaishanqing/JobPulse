"""Add source_jd_version_id to evidence_sources.

TEMP-LAG lineage: the governance evidence row now carries the immutable
SourceJDVersion.id independently from ``source_version`` (source-fact version).
This is the only field used by the InsightCard path to fetch crawler
``crawl_time``.

Revision ID: 20260819_76
Revises: 20260818_75
"""

from alembic import op
import sqlalchemy as sa


revision = "20260819_76"
down_revision = "20260818_75"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("evidence_sources")}
    if "source_jd_version_id" not in columns:
        op.add_column(
            "evidence_sources",
            sa.Column("source_jd_version_id", sa.String(36), nullable=True),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("evidence_sources")}
    if "source_jd_version_id" in columns:
        op.drop_column("evidence_sources", "source_jd_version_id")
