"""add immutable JD publication snapshots

Revision ID: 20260723_26
Revises: 20260723_25
"""

from alembic import op
import sqlalchemy as sa


revision = "20260723_26"
down_revision = "20260723_25"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("jd_publications"):
        _install_immutability()
        return
    op.create_table(
        "jd_publications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "parse_result_id",
            sa.String(36),
            sa.ForeignKey("jd_parse_results.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "jd_id",
            sa.String(36),
            sa.ForeignKey("job_descriptions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "source_jd_id",
            sa.String(36),
            sa.ForeignKey("source_jds.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "source_jd_version_id",
            sa.String(36),
            sa.ForeignKey("source_jd_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "extraction_task_id",
            sa.String(36),
            sa.ForeignKey("extraction_tasks.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("document_id", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("normalization_schema_version", sa.String(32), nullable=False),
        sa.Column("content_fingerprint", sa.String(71), nullable=False),
        sa.Column("idempotency_key", sa.String(180), nullable=False),
        sa.Column("snapshot_payload", sa.JSON(), nullable=False),
        sa.Column("published_by", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "parse_result_id", name="uq_jd_publications_parse_result_id"
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_jd_publications_idempotency_key"
        ),
    )
    op.create_index(
        "ix_jd_publications_parse_result_id",
        "jd_publications",
        ["parse_result_id"],
    )
    op.create_index("ix_jd_publications_jd_id", "jd_publications", ["jd_id"])
    op.create_index(
        "ix_jd_publications_source_jd_version_id",
        "jd_publications",
        ["source_jd_version_id"],
    )
    op.create_index(
        "ix_jd_publications_content_fingerprint",
        "jd_publications",
        ["content_fingerprint"],
    )
    _install_immutability()


def _install_immutability() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute(
            "CREATE TRIGGER IF NOT EXISTS jd_publications_reject_update "
            "BEFORE UPDATE ON jd_publications BEGIN "
            "SELECT RAISE(ABORT, 'JDPublication records are immutable'); END"
        )
        op.execute(
            "CREATE TRIGGER IF NOT EXISTS jd_publications_reject_delete "
            "BEFORE DELETE ON jd_publications BEGIN "
            "SELECT RAISE(ABORT, 'JDPublication records are immutable'); END"
        )
    elif dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_jd_publication_mutation() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'JDPublication records are immutable'
                    USING ERRCODE = 'integrity_constraint_violation';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            "CREATE TRIGGER jd_publications_reject_mutation "
            "BEFORE UPDATE OR DELETE ON jd_publications "
            "FOR EACH ROW EXECUTE FUNCTION reject_jd_publication_mutation()"
        )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS jd_publications_reject_delete")
        op.execute("DROP TRIGGER IF EXISTS jd_publications_reject_update")
    elif dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS jd_publications_reject_mutation "
            "ON jd_publications"
        )
        op.execute("DROP FUNCTION IF EXISTS reject_jd_publication_mutation()")
    op.drop_index(
        "ix_jd_publications_content_fingerprint", table_name="jd_publications"
    )
    op.drop_index(
        "ix_jd_publications_source_jd_version_id", table_name="jd_publications"
    )
    op.drop_index("ix_jd_publications_jd_id", table_name="jd_publications")
    op.drop_index(
        "ix_jd_publications_parse_result_id", table_name="jd_publications"
    )
    op.drop_table("jd_publications")
