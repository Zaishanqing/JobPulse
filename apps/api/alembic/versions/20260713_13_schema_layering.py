"""add explicit schema versions for layered contracts

Revision ID: 20260713_13
Revises: 20260713_12
Create Date: 2026-07-13
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260713_13"
down_revision: Union[str, Sequence[str], None] = "20260713_12"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    existing = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("jd_parse_results")
    }
    additions = {
        "schema_version": sa.Column(
            "schema_version", sa.String(length=32), nullable=False, server_default="v2"
        ),
        "normalization_schema_version": sa.Column(
            "normalization_schema_version",
            sa.String(length=32),
            nullable=False,
            server_default="v2",
        ),
    }
    for name, column in additions.items():
        if name not in existing:
            op.add_column("jd_parse_results", column)


def downgrade() -> None:
    existing = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("jd_parse_results")
    }
    for name in ("normalization_schema_version", "schema_version"):
        if name in existing:
            op.drop_column("jd_parse_results", name)
