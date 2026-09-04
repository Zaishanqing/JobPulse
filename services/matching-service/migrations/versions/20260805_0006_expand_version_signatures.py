"""expand persisted matching version signatures

Revision ID: 20260805_0006
Revises: 20260729_0005
"""

import sqlalchemy as sa
from alembic import op

revision = "20260805_0006"
down_revision = "20260729_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table_name in ("evaluation_tasks", "persisted_evaluations"):
        with op.batch_alter_table(table_name) as batch:
            batch.alter_column(
                "version_signature",
                existing_type=sa.String(length=500),
                type_=sa.String(length=2000),
                existing_nullable=False,
            )
    with op.batch_alter_table("audit_records") as batch:
        batch.alter_column(
            "algorithm_version",
            existing_type=sa.String(length=500),
            type_=sa.String(length=2000),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("audit_records") as batch:
        batch.alter_column(
            "algorithm_version",
            existing_type=sa.String(length=2000),
            type_=sa.String(length=500),
            existing_nullable=False,
        )
    for table_name in ("persisted_evaluations", "evaluation_tasks"):
        with op.batch_alter_table(table_name) as batch:
            batch.alter_column(
                "version_signature",
                existing_type=sa.String(length=2000),
                type_=sa.String(length=500),
                existing_nullable=False,
            )
