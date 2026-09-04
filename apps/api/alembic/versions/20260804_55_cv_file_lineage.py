"""persist source file and text extraction lineage for CV versions

Revision ID: 20260804_55
Revises: 20260803_52
"""

from alembic import op
import sqlalchemy as sa


revision = "20260804_55"
down_revision = "20260803_52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {
        column["name"] for column in inspector.get_columns("source_cv_versions")
    }
    additions = [
        ("source_file_id", sa.Column("source_file_id", sa.String(length=36), nullable=True)),
        ("original_filename", sa.Column("original_filename", sa.String(length=255), nullable=True)),
        ("content_type", sa.Column("content_type", sa.String(length=128), nullable=True)),
        ("source_file_sha256", sa.Column("source_file_sha256", sa.String(length=71), nullable=True)),
        ("extraction_method", sa.Column("extraction_method", sa.String(length=32), nullable=True)),
        ("extraction_provider", sa.Column("extraction_provider", sa.String(length=64), nullable=True)),
        (
            "extraction_provider_version",
            sa.Column("extraction_provider_version", sa.String(length=64), nullable=True),
        ),
        (
            "text_extraction_status",
            sa.Column("text_extraction_status", sa.String(length=16), nullable=True),
        ),
        ("raw_text_sha256", sa.Column("raw_text_sha256", sa.String(length=71), nullable=True)),
        ("page_count", sa.Column("page_count", sa.Integer(), nullable=True)),
        ("quality_flags", sa.Column("quality_flags", sa.JSON(), nullable=True)),
    ]
    fk_names = {
        item["name"] for item in inspector.get_foreign_keys("source_cv_versions")
    }
    with op.batch_alter_table("source_cv_versions") as batch_op:
        for name, column in additions:
            if name not in columns:
                batch_op.add_column(column)
        if "source_file_id" not in columns and "fk_source_cv_versions_source_file_id" not in fk_names:
            batch_op.create_foreign_key(
                "fk_source_cv_versions_source_file_id",
                "file_assets",
                ["source_file_id"],
                ["id"],
                ondelete="RESTRICT",
            )


def downgrade() -> None:
    with op.batch_alter_table("source_cv_versions") as batch_op:
        batch_op.drop_constraint(
            "fk_source_cv_versions_source_file_id", type_="foreignkey"
        )
        for name in (
            "source_file_id",
            "original_filename",
            "content_type",
            "source_file_sha256",
            "extraction_method",
            "extraction_provider",
            "extraction_provider_version",
            "text_extraction_status",
            "raw_text_sha256",
            "page_count",
            "quality_flags",
        ):
            batch_op.drop_column(name)
