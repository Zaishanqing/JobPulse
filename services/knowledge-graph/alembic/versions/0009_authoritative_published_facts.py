"""add versioned authoritative published JD fact imports

Revision ID: 0009_authoritative_published_facts
Revises: 0008_graph_edit_concurrency
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op


revision = "0009_authoritative_published_facts"
down_revision = "0008_graph_edit_concurrency"
branch_labels = None
depends_on = None


def upgrade():
    additions = {
        "source_system": sa.Column(
            "source_system", sa.String(40), nullable=False,
            server_default="knowledge-graph",
        ),
        "fact_authority": sa.Column(
            "fact_authority", sa.String(30), nullable=False,
            server_default="legacy_local",
        ),
        "source_fact_id": sa.Column("source_fact_id", sa.String(80)),
        "source_fact_version": sa.Column("source_fact_version", sa.String(80)),
        "source_schema_version": sa.Column("source_schema_version", sa.String(30)),
        "content_hash": sa.Column("content_hash", sa.String(64)),
    }
    if context.is_offline_mode():
        for column in additions.values():
            op.add_column("jd_documents", column)
        op.create_index(
            "ix_jd_documents_fact_authority", "jd_documents", ["fact_authority"]
        )
        _create_import_table()
        return

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {item["name"] for item in inspector.get_columns("jd_documents")}
    for name, column in additions.items():
        if name not in columns:
            op.add_column("jd_documents", column)
    indexes = {item["name"] for item in inspector.get_indexes("jd_documents")}
    if "ix_jd_documents_fact_authority" not in indexes:
        op.create_index(
            "ix_jd_documents_fact_authority", "jd_documents", ["fact_authority"]
        )
    if inspector.has_table("published_fact_imports"):
        return
    _create_import_table()


def _create_import_table():
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
        sa.UniqueConstraint(
            "source_system", "source_fact_id", "source_fact_version",
            name="uq_published_fact_source_version",
        ),
    )
    op.create_index(
        "ix_published_fact_imports_document_id", "published_fact_imports",
        ["document_id"],
    )


def downgrade():
    raise RuntimeError("Migration 0009 is forward-only and cannot be downgraded")
