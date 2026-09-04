"""extend the stable baseline with authoritative published JD facts"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "baseline_0007"
down_revision = "baseline_0006"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {item["name"] for item in inspector.get_columns("jd_documents")}
    additions = [sa.Column(
        "source_system", sa.String(40), nullable=False,
        server_default="knowledge-graph",
    ), sa.Column(
        "fact_authority", sa.String(30), nullable=False,
        server_default="legacy_local",
    ), sa.Column("source_fact_id", sa.String(80)),
        sa.Column("source_fact_version", sa.String(80)),
        sa.Column("source_schema_version", sa.String(30)),
        sa.Column("content_hash", sa.String(64))]
    for column in additions:
        if column.name not in columns:
            op.add_column("jd_documents", column)
    indexes = {item["name"] for item in inspector.get_indexes("jd_documents")}
    if "ix_jd_documents_fact_authority" not in indexes:
        op.create_index(
            "ix_jd_documents_fact_authority", "jd_documents", ["fact_authority"]
        )
    if inspector.has_table("published_fact_imports"):
        return
    op.create_table(
        "published_fact_imports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_system", sa.String(40), nullable=False),
        sa.Column("source_fact_id", sa.String(80), nullable=False),
        sa.Column("source_fact_version", sa.String(80), nullable=False),
        sa.Column("source_schema_version", sa.String(30), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("document_id", sa.String(80), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["jd_documents.document_id"]),
        sa.UniqueConstraint("source_system", "source_fact_id", "source_fact_version"),
    )
    op.create_index(
        "ix_published_fact_imports_document_id", "published_fact_imports",
        ["document_id"],
    )


def downgrade():
    op.drop_index(
        "ix_published_fact_imports_document_id", table_name="published_fact_imports"
    )
    op.drop_table("published_fact_imports")
    op.drop_index("ix_jd_documents_fact_authority", table_name="jd_documents")
    for name in (
        "content_hash", "source_schema_version", "source_fact_version",
        "source_fact_id", "fact_authority", "source_system",
    ):
        op.drop_column("jd_documents", name)


