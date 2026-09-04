"""Add salary unit to enterprise jobs.

Revision ID: 20260830_80
Revises: 20260829_79

The existing salary_min/salary_max columns are plain amounts and the UI has
always displayed them as monthly pay.  This migration makes the unit explicit
so enterprise users can choose year/month/day when creating a job.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260830_80"
down_revision = "20260829_79"
branch_labels = None
depends_on = None


def _column_names(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    columns = _column_names("enterprise_jobs")
    if "salary_unit" not in columns:
        with op.batch_alter_table("enterprise_jobs") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "salary_unit",
                    sa.String(16),
                    nullable=False,
                    server_default="month",
                )
            )
            batch_op.create_check_constraint(
                "ck_enterprise_jobs_salary_unit_allowed",
                "salary_unit in ('year', 'month', 'day')",
            )


def downgrade() -> None:
    columns = _column_names("enterprise_jobs")
    if "salary_unit" in columns:
        with op.batch_alter_table("enterprise_jobs") as batch_op:
            batch_op.drop_constraint(
                "ck_enterprise_jobs_salary_unit_allowed", type_="check"
            )
            batch_op.drop_column("salary_unit")
