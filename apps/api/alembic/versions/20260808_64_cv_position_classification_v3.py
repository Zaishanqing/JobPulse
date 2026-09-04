"""Store CV role position-taxonomy.v3 classifications.

Revision ID: 20260808_64
Revises: 20260808_63
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260808_64"
down_revision: str | None = "20260808_63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("cv_position_classifications"):
        return
    op.create_table(
        "cv_position_classifications",
        sa.Column(
            "resume_id",
            sa.String(length=36),
            sa.ForeignKey("resumes.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("taxonomy_version", sa.String(length=64), nullable=False),
        sa.Column("classifications", sa.JSON(), nullable=False),
        sa.Column("source_run_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("cv_position_classifications"):
        op.drop_table("cv_position_classifications")
