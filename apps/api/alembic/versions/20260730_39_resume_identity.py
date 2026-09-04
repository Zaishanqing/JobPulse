"""add user-facing resume identity fields

Revision ID: 20260730_39
Revises: 20260729_38
"""

import sqlalchemy as sa
from alembic import op


revision = "20260730_39"
down_revision = "20260729_38"
branch_labels = None
depends_on = None


def _column_names() -> set[str]:
    """Allow a retry after SQLite has partially committed ADD COLUMN."""
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("resumes")
    }


def upgrade() -> None:
    columns = _column_names()
    if "display_name" not in columns:
        op.add_column(
            "resumes",
            sa.Column("display_name", sa.String(length=120), nullable=True),
        )
    if "original_filename" not in columns:
        op.add_column(
            "resumes",
            sa.Column("original_filename", sa.String(length=255), nullable=True),
        )

    # Preserve the uploaded filename independently from mutable file storage
    # metadata, then give every legacy row a stable user-facing fallback.
    op.execute(
        sa.text(
            "UPDATE resumes SET original_filename = "
            "(SELECT filename FROM file_assets WHERE file_assets.id = resumes.file_id) "
            "WHERE file_id IS NOT NULL AND original_filename IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE resumes SET display_name = "
            "CASE "
            "WHEN original_filename IS NOT NULL THEN SUBSTR(original_filename, 1, 120) "
            "WHEN source_type = 'image' THEN '图片简历' "
            "WHEN source_type = 'file' THEN '上传简历' "
            "ELSE '文本简历' END "
            "WHERE display_name IS NULL"
        )
    )


def downgrade() -> None:
    columns = _column_names()
    if "original_filename" in columns:
        op.drop_column("resumes", "original_filename")
    if "display_name" in columns:
        op.drop_column("resumes", "display_name")
