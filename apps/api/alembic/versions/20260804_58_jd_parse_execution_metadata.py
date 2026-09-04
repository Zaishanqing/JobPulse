"""persist JD parse execution metadata

Revision ID: 20260804_58
Revises: 20260804_57
"""

import sqlalchemy as sa
from alembic import op


revision = "20260804_58"
down_revision = "20260804_57"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jd_parse_results",
        sa.Column("execution_metadata", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("jd_parse_results") as batch_op:
        batch_op.drop_column("execution_metadata")
