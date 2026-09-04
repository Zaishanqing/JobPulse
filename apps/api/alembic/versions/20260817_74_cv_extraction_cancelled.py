"""add explicit CV extraction cancelled status

Revision ID: 20260817_74
Revises: 20260816_73
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260817_74"
down_revision: str | None = "20260816_73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("cv_extraction_tasks") as batch:
        batch.drop_constraint("ck_cv_extraction_tasks_status", type_="check")
        batch.create_check_constraint(
            "ck_cv_extraction_tasks_status",
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
        )


def downgrade() -> None:
    op.execute("UPDATE cv_extraction_tasks SET status = 'failed' WHERE status = 'cancelled'")
    with op.batch_alter_table("cv_extraction_tasks") as batch:
        batch.drop_constraint("ck_cv_extraction_tasks_status", type_="check")
        batch.create_check_constraint(
            "ck_cv_extraction_tasks_status",
            "status IN ('pending', 'running', 'succeeded', 'failed')",
        )
