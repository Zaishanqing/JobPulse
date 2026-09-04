"""persist truthful OCR results

Revision ID: 20260712_04
Revises: 20260712_03
Create Date: 2026-07-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260712_04"
down_revision: Union[str, Sequence[str], None] = "20260712_03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "ocr_results" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "ocr_results",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("edited", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ocr_results_created_by"), "ocr_results", ["created_by"])


def downgrade() -> None:
    op.drop_index(op.f("ix_ocr_results_created_by"), table_name="ocr_results")
    op.drop_table("ocr_results")
