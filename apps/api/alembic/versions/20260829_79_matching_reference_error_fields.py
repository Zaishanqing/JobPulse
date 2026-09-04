"""Persist failure details on matching service references.

Revision ID: 20260829_79
Revises: 20260827_78

Ranking candidates rejected by the matching service previously failed before
any reference row was written, leaving the ranking stuck in a non-terminal
"preliminary" state forever.  These nullable columns let a failed reference
record its terminal error so the ranking can finish and the UI can explain
why a position could not be scored.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260829_79"
down_revision = "20260827_78"
branch_labels = None
depends_on = None


def _column_names(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    columns = _column_names("matching_service_references")
    if "error_code" not in columns:
        op.add_column(
            "matching_service_references",
            sa.Column("error_code", sa.String(64), nullable=True),
        )
    if "error_message" not in columns:
        op.add_column(
            "matching_service_references",
            sa.Column("error_message", sa.String(500), nullable=True),
        )


def downgrade() -> None:
    columns = _column_names("matching_service_references")
    if "error_message" in columns:
        op.drop_column("matching_service_references", "error_message")
    if "error_code" in columns:
        op.drop_column("matching_service_references", "error_code")
