"""backfill legacy authoritative document source versions

Revision ID: 0023_backfill_document_source_versions
Revises: 0022_widen_watermark_lineage_version
"""
from alembic import op


revision = "0023_backfill_document_source_versions"
down_revision = "0022_widen_watermark_lineage_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE jd_documents SET source_version = 'legacy-' || substr(document_id, 1, 32) "
        "WHERE source_version IS NULL"
    )


def downgrade() -> None:
    pass
