"""persist editable RAG generations

Revision ID: 20260712_11
Revises: 20260712_10
Create Date: 2026-07-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260712_11"
down_revision: Union[str, Sequence[str], None] = "20260712_10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "rag_generations" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "rag_generations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("need_review", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("confirmed_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status in ('draft', 'confirmed')",
            name="ck_rag_generations_status_allowed",
        ),
        sa.ForeignKeyConstraint(["confirmed_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_rag_generations_created_by"), "rag_generations", ["created_by"]
    )


def downgrade() -> None:
    if "rag_generations" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("rag_generations")
