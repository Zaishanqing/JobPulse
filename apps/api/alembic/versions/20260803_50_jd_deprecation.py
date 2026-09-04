"""Add JD deprecation flag.

Deprecated JDs stay in the database for audit lineage, but their parse results
are retired and their active review tasks are closed.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260803_50"
down_revision = "20260803_49"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_descriptions",
        sa.Column(
            "is_deprecated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "ix_job_descriptions_is_deprecated",
        "job_descriptions",
        ["is_deprecated"],
    )


def downgrade() -> None:
    op.drop_index("ix_job_descriptions_is_deprecated", table_name="job_descriptions")
    op.drop_column("job_descriptions", "is_deprecated")
