"""Add canonical cleaned JD text.

The cleaned text is produced by the formal Extraction cleaning stage and is
the Evidence base for extraction results; the original raw text stays as
audit lineage.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260803_51"
down_revision = "20260803_50"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_descriptions",
        sa.Column("cleaned_text", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job_descriptions", "cleaned_text")
