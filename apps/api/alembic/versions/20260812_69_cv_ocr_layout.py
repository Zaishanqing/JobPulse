"""Persist OCR layout boxes for field-level CV review.

Revision ID: 20260812_69
Revises: 20260812_68
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260812_69"
down_revision: str | None = "20260812_68"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("source_cv_versions")
    }
    if "ocr_layout" not in columns:
        op.add_column("source_cv_versions", sa.Column("ocr_layout", sa.JSON(), nullable=True))


def downgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("source_cv_versions")
    }
    if "ocr_layout" in columns:
        op.drop_column("source_cv_versions", "ocr_layout")
