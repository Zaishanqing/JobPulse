"""add candidate authorization grant version

Revision ID: 20260729_38
Revises: 20260729_37
"""

import sqlalchemy as sa
from alembic import op


revision = "20260729_38"
down_revision = "20260729_37"
branch_labels = None
depends_on = None


def _column_names() -> set[str]:
    """Read the live schema so a retry can recover from partial SQLite DDL."""
    inspector = sa.inspect(op.get_bind())
    if "candidate_submissions" not in inspector.get_table_names():
        return set()
    return {
        column["name"]
        for column in inspector.get_columns("candidate_submissions")
    }


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "candidate_submissions" not in inspector.get_table_names():
        raise RuntimeError("candidate_submissions table is required before grant migration")

    columns = {
        column["name"]: column
        for column in inspector.get_columns("candidate_submissions")
    }
    if "grant_version" not in columns:
        op.add_column(
            "candidate_submissions",
            sa.Column(
                "grant_version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
        )

    # SQLite keeps ADD COLUMN outside a transactional rollback. Retain its
    # harmless default there so a retry can safely continue after partial DDL.
    bind.execute(
        sa.text(
            "UPDATE candidate_submissions SET grant_version = 1 "
            "WHERE grant_version IS NULL"
        )
    )
    if bind.dialect.name != "sqlite":
        column = {
            column["name"]: column
            for column in sa.inspect(bind).get_columns("candidate_submissions")
        }["grant_version"]
        op.alter_column(
            "candidate_submissions",
            "grant_version",
            existing_type=column["type"],
            type_=sa.Integer(),
            nullable=False,
            server_default=None,
        )


def downgrade() -> None:
    if "grant_version" in _column_names():
        op.drop_column("candidate_submissions", "grant_version")
