"""add trend intelligence provider projections

Revision ID: 20260730_40
Revises: 20260730_39
"""

import sqlalchemy as sa
from alembic import op

revision = "20260730_40"
down_revision = "20260730_39"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("predicted_positions") as batch:
        batch.add_column(sa.Column("provider_run_id", sa.String(80), nullable=True))
        batch.add_column(sa.Column("candidate_key", sa.String(128), nullable=True))
        batch.add_column(sa.Column("industry_domain", sa.String(255), nullable=True))
        batch.add_column(sa.Column("emergence_score", sa.Float(), nullable=True))
        batch.add_column(sa.Column("score_components", sa.JSON(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("algorithm_version", sa.String(128), nullable=True))
        batch.add_column(sa.Column("formula_version", sa.String(128), nullable=True))
        batch.add_column(sa.Column("window_start", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("window_end", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("source_coverage", sa.Float(), nullable=True))
        batch.add_column(sa.Column("missing_sources", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("quality_flags", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("evidence_references", sa.JSON(), nullable=False, server_default="[]"))
        batch.create_unique_constraint("uq_predicted_positions_provider_candidate", ["provider_run_id", "candidate_key"])
        batch.create_index("ix_predicted_positions_provider_run_id", ["provider_run_id"])
    with op.batch_alter_table("trend_sources") as batch:
        batch.add_column(sa.Column("provider_run_id", sa.String(80), nullable=True))
        batch.add_column(sa.Column("external_source_id", sa.String(256), nullable=True))
        batch.add_column(sa.Column("source_version", sa.String(128), nullable=True))
        batch.add_column(sa.Column("content_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("snapshot_reference", sa.String(128), nullable=True))
        batch.add_column(sa.Column("extraction_version", sa.String(128), nullable=True))
        batch.add_column(sa.Column("source_metadata", sa.JSON(), nullable=False, server_default="{}"))
        batch.create_unique_constraint("uq_trend_sources_provider_snapshot", ["provider_run_id", "snapshot_reference"])
        batch.create_index("ix_trend_sources_provider_run_id", ["provider_run_id"])


def downgrade() -> None:
    with op.batch_alter_table("trend_sources") as batch:
        batch.drop_index("ix_trend_sources_provider_run_id")
        batch.drop_constraint("uq_trend_sources_provider_snapshot", type_="unique")
        for column in ("source_metadata", "extraction_version", "snapshot_reference", "captured_at", "content_hash", "source_version", "external_source_id", "provider_run_id"):
            batch.drop_column(column)
    with op.batch_alter_table("predicted_positions") as batch:
        batch.drop_index("ix_predicted_positions_provider_run_id")
        batch.drop_constraint("uq_predicted_positions_provider_candidate", type_="unique")
        for column in ("evidence_references", "quality_flags", "missing_sources", "source_coverage", "window_end", "window_start", "formula_version", "algorithm_version", "score_components", "emergence_score", "industry_domain", "candidate_key", "provider_run_id"):
            batch.drop_column(column)
