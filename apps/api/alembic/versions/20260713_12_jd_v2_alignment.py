"""persist V2 JD extraction and normalization results

Revision ID: 20260713_12
Revises: 20260712_11
Create Date: 2026-07-13
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260713_12"
down_revision: Union[str, Sequence[str], None] = "20260712_11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns("jd_parse_results")}
    additions = {
        "extraction_result": sa.Column("extraction_result", sa.JSON(), nullable=True),
        "normalized_result": sa.Column("normalized_result", sa.JSON(), nullable=True),
        "workflow_status": sa.Column(
            "workflow_status", sa.String(length=32), nullable=False, server_default="draft"
        ),
    }
    for name, column in additions.items():
        if name not in existing:
            op.add_column("jd_parse_results", column)


def downgrade() -> None:
    existing = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("jd_parse_results")
    }
    for name in ("workflow_status", "normalized_result", "extraction_result"):
        if name in existing:
            op.drop_column("jd_parse_results", name)
