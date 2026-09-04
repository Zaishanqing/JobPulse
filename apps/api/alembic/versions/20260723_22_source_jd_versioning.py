"""add immutable source JD version storage

Revision ID: 20260723_22
Revises: 20260722_21
"""

from alembic import op
import sqlalchemy as sa


revision = "20260723_22"
down_revision = "20260722_21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    source_exists = inspector.has_table("source_jds")
    versions_exist = inspector.has_table("source_jd_versions")
    if source_exists != versions_exist:
        raise RuntimeError("Existing SourceJD schema is incomplete")
    if source_exists:
        _validate_existing_schema(inspector)
        _install_immutability()
        return

    op.create_table(
        "source_jds",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_platform", sa.String(length=64), nullable=False),
        sa.Column("source_record_id", sa.String(length=255), nullable=False),
        sa.Column("latest_version_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_platform", "source_record_id", name="uq_source_jds_platform_record"
        ),
    )
    op.create_index("ix_source_jds_source_platform", "source_jds", ["source_platform"])
    op.create_index("ix_source_jds_latest_version_id", "source_jds", ["latest_version_id"])

    op.create_table(
        "source_jd_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_jd_id", sa.String(length=36), nullable=False),
        sa.Column("content_hash", sa.String(length=71), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("raw_html", sa.Text(), nullable=True),
        sa.Column("source_url", sa.String(length=2048), nullable=True),
        sa.Column("crawl_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("job_title_raw", sa.String(length=512), nullable=True),
        sa.Column("company_name_raw", sa.String(length=512), nullable=True),
        sa.Column("region_raw", sa.String(length=255), nullable=True),
        sa.Column("publish_time_raw", sa.String(length=255), nullable=True),
        sa.Column("text_canonicalization_version", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_jd_id"], ["source_jds.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_jd_id", "content_hash", name="uq_source_jd_versions_source_hash"
        ),
    )
    op.create_index(
        "ix_source_jd_versions_source_jd_id", "source_jd_versions", ["source_jd_id"]
    )
    op.create_index(
        "ix_source_jd_versions_created_at", "source_jd_versions", ["created_at"]
    )
    with op.batch_alter_table("source_jds") as batch:
        batch.create_foreign_key(
            "fk_source_jds_latest_version_id",
            "source_jd_versions",
            ["latest_version_id"],
            ["id"],
        )

    _install_immutability()


def _validate_existing_schema(inspector) -> None:
    required_source = {
        "id", "source_platform", "source_record_id", "latest_version_id", "created_at", "updated_at"
    }
    base_version = {
        "id", "source_jd_id", "content_hash", "schema_version", "raw_text", "raw_payload",
        "raw_html", "source_url", "crawl_time", "job_title_raw", "company_name_raw",
        "region_raw", "publish_time_raw", "text_canonicalization_version", "created_at",
    }
    source_columns = {item["name"] for item in inspector.get_columns("source_jds")}
    version_columns = {item["name"] for item in inspector.get_columns("source_jd_versions")}
    if "source_version" in version_columns:
        # The explicit source-version model is the stable identity even when a
        # content_hash traceability column is also present.  Prefer it over the
        # historical content-hash unique constraint.
        required_version = base_version - {"content_hash"} | {"source_version"}
        version_identity_unique = ("source_jd_id", "source_version")
    elif "content_hash" in version_columns:
        required_version = base_version
        version_identity_unique = ("source_jd_id", "content_hash")
    else:
        raise RuntimeError(
            "Existing SourceJD schema lacks a version identity column"
        )
    missing = (required_source - source_columns) | (required_version - version_columns)
    if missing:
        raise RuntimeError(f"Existing SourceJD schema is incomplete; missing columns: {sorted(missing)}")
    source_uniques = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("source_jds")
    }
    version_uniques = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("source_jd_versions")
    }
    if ("source_platform", "source_record_id") not in source_uniques:
        raise RuntimeError("Existing SourceJD schema lacks source identity uniqueness")
    if version_identity_unique not in version_uniques:
        raise RuntimeError("Existing SourceJD schema lacks version identity uniqueness")


def _install_immutability() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute(
            "CREATE TRIGGER source_jd_versions_reject_update "
            "BEFORE UPDATE ON source_jd_versions BEGIN "
            "SELECT RAISE(ABORT, 'SourceJDVersion records are immutable'); END"
        )
        op.execute(
            "CREATE TRIGGER source_jd_versions_reject_delete "
            "BEFORE DELETE ON source_jd_versions BEGIN "
            "SELECT RAISE(ABORT, 'SourceJDVersion records are immutable'); END"
        )
    elif dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_source_jd_version_mutation() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'SourceJDVersion records are immutable'
                    USING ERRCODE = 'integrity_constraint_violation';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            "CREATE TRIGGER source_jd_versions_reject_mutation "
            "BEFORE UPDATE OR DELETE ON source_jd_versions "
            "FOR EACH ROW EXECUTE FUNCTION reject_source_jd_version_mutation()"
        )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS source_jd_versions_reject_delete")
        op.execute("DROP TRIGGER IF EXISTS source_jd_versions_reject_update")
    elif dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS source_jd_versions_reject_mutation ON source_jd_versions")
        op.execute("DROP FUNCTION IF EXISTS reject_source_jd_version_mutation()")
    with op.batch_alter_table("source_jds") as batch:
        batch.drop_constraint("fk_source_jds_latest_version_id", type_="foreignkey")
    op.drop_index("ix_source_jd_versions_created_at", table_name="source_jd_versions")
    op.drop_index("ix_source_jd_versions_source_jd_id", table_name="source_jd_versions")
    op.drop_table("source_jd_versions")
    op.drop_index("ix_source_jds_latest_version_id", table_name="source_jds")
    op.drop_index("ix_source_jds_source_platform", table_name="source_jds")
    op.drop_table("source_jds")
