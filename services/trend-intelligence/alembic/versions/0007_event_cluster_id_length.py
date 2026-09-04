"""Widen event_cluster_id columns to fit uuid4 cluster ids.

The domain generates event cluster ids with uuid4 (36 characters), while the
columns were declared String(32). SQLite-based tests never enforced the
length, but PostgreSQL rejects the insert, so widen both columns.

Revision ID: 0007_event_cluster_id_length
Revises: 0006_trend_change_analyses
"""

import sqlalchemy as sa
from alembic import op


revision = "0007_event_cluster_id_length"
down_revision = "0006_trend_change_analyses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("source_snapshots", "event_cluster_id", type_=sa.String(36))
    op.alter_column("evidence", "event_cluster_id", type_=sa.String(36))


def downgrade() -> None:
    op.alter_column("source_snapshots", "event_cluster_id", type_=sa.String(32))
    op.alter_column("evidence", "event_cluster_id", type_=sa.String(32))
