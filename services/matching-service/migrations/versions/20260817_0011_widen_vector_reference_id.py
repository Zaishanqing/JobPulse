"""Widen legacy vector reference identifiers.

Revision ID: 20260817_0011
Revises: 20260811_0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260817_0011"
down_revision: str | None = "20260811_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    column = next(
        item
        for item in sa.inspect(op.get_bind()).get_columns("vector_index_references")
        if item["name"] == "reference_id"
    )
    if getattr(column["type"], "length", None) != 1024:
        with op.batch_alter_table("vector_index_references") as batch:
            batch.alter_column(
                "reference_id",
                existing_type=column["type"],
                type_=sa.String(length=1024),
                existing_nullable=False,
            )


def downgrade() -> None:
    with op.batch_alter_table("vector_index_references") as batch:
        batch.alter_column(
            "reference_id",
            existing_type=sa.String(length=1024),
            type_=sa.String(length=64),
            existing_nullable=False,
        )
