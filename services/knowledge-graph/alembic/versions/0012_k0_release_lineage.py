"""add K0 release import ledger and graph release lineage

Revision ID: 0012_k0_release_lineage
Revises: 0011_traceskill_innovation_planes
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0012_k0_release_lineage"
down_revision = "0011_traceskill_innovation_planes"
branch_labels = None
depends_on = None

IMMUTABLE_TABLES = (
    "release_import_batches",
    "release_import_items",
    "published_fact_release_links",
)


def _identity_columns() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade():
    op.create_table(
        "release_import_batches",
        *_identity_columns(),
        sa.Column("release_id", sa.String(128), nullable=False),
        sa.Column("manifest_hash", sa.String(64), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.UniqueConstraint("release_id"),
        sa.UniqueConstraint("manifest_hash"),
    )
    op.create_index("ix_release_import_batches_release_id", "release_import_batches", ["release_id"])
    op.create_table(
        "release_import_items",
        *_identity_columns(),
        sa.Column("release_id", sa.String(128), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_system", sa.String(40), nullable=False),
        sa.Column("source_fact_id", sa.String(80), nullable=False),
        sa.Column("source_fact_version", sa.String(80), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("document_id", sa.String(80), nullable=False),
        sa.ForeignKeyConstraint(["release_id"], ["release_import_batches.release_id"]),
        sa.ForeignKeyConstraint(["document_id"], ["jd_documents.document_id"]),
        sa.UniqueConstraint("release_id", "ordinal"),
        sa.UniqueConstraint("release_id", "source_system", "source_fact_id", "source_fact_version"),
    )
    op.create_index("ix_release_import_items_release_id", "release_import_items", ["release_id"])
    op.create_table(
        "published_fact_release_links",
        *_identity_columns(),
        sa.Column("published_fact_import_id", sa.Integer(), nullable=False),
        sa.Column("release_id", sa.String(128), nullable=False),
        sa.ForeignKeyConstraint(["published_fact_import_id"], ["published_fact_imports.id"]),
        sa.ForeignKeyConstraint(["release_id"], ["release_import_batches.release_id"]),
        sa.UniqueConstraint("published_fact_import_id", "release_id"),
    )
    op.create_index("ix_published_fact_release_links_published_fact_import_id", "published_fact_release_links", ["published_fact_import_id"])
    op.create_index("ix_published_fact_release_links_release_id", "published_fact_release_links", ["release_id"])
    op.add_column("graph_build_runs", sa.Column("release_id", sa.String(128)))
    op.create_index("ix_graph_build_runs_release_id", "graph_build_runs", ["release_id"])
    op.add_column("graph_versions", sa.Column("release_id", sa.String(128)))
    op.create_index("ix_graph_versions_release_id", "graph_versions", ["release_id"])
    _create_immutability_guards()


def _create_immutability_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for table in IMMUTABLE_TABLES:
            op.execute(
                f"CREATE TRIGGER trg_{table}_reject_update BEFORE UPDATE ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{table} is immutable'); END"
            )
            op.execute(
                f"CREATE TRIGGER trg_{table}_reject_delete BEFORE DELETE ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{table} is immutable'); END"
            )
    elif dialect == "postgresql":
        op.execute("""
            CREATE FUNCTION reject_k0_release_mutation() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
                RAISE EXCEPTION 'K0 release ledger is immutable'
                    USING ERRCODE = 'integrity_constraint_violation';
            END; $$
        """)
        for table in IMMUTABLE_TABLES:
            op.execute(
                f"CREATE TRIGGER trg_{table}_reject_update BEFORE UPDATE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION reject_k0_release_mutation()"
            )
            op.execute(
                f"CREATE TRIGGER trg_{table}_reject_delete BEFORE DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION reject_k0_release_mutation()"
            )
    else:
        raise RuntimeError(f"K0 release migration does not support dialect {dialect!r}")


def downgrade():
    raise RuntimeError("Migration 0012 is forward-only and cannot be downgraded")
