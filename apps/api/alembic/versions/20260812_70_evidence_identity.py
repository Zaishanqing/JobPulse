"""Add formal source and independence identity to Governance Evidence.

Revision ID: 20260812_70
Revises: 20260812_69
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260812_70"
down_revision: str | None = "20260812_69"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_COLUMNS = (
    sa.Column("source_platform", sa.String(length=128), nullable=True),
    sa.Column("enterprise_id", sa.String(length=128), nullable=True),
    sa.Column("template_cluster_id", sa.String(length=128), nullable=True),
    sa.Column("source_version", sa.String(length=128), nullable=True),
    sa.Column("source_fact_id", sa.String(length=128), nullable=True),
    sa.Column("source_jd_id", sa.String(length=128), nullable=True),
)


def upgrade() -> None:
    existing = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("evidence_sources")
    }
    for column in _COLUMNS:
        if column.name not in existing:
            op.add_column("evidence_sources", column)


def downgrade() -> None:
    existing = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("evidence_sources")
    }
    for column in reversed(_COLUMNS):
        if column.name in existing:
            op.drop_column("evidence_sources", column.name)
