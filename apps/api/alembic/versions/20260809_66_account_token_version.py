"""Add lightweight account token versioning.

Revision ID: 20260809_66
Revises: 20260809_65
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260809_66"
down_revision: str | None = "20260809_65"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "token_version" not in columns:
        op.add_column(
            "users",
            sa.Column(
                "token_version",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "token_version" in columns:
        op.drop_column("users", "token_version")
