"""persist enterprise candidate decisions

Revision ID: 20260712_08
Revises: 20260712_07
Create Date: 2026-07-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260712_08"
down_revision: Union[str, Sequence[str], None] = "20260712_07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "candidate_decisions" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "candidate_decisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("enterprise_job_id", sa.String(length=36), nullable=False),
        sa.Column("resume_id", sa.String(length=36), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("decided_by", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("decision in ('fit', 'unfit')", name="ck_candidate_decision_allowed"),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["enterprise_job_id"], ["enterprise_jobs.id"]),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "enterprise_job_id", "resume_id", name="uq_candidate_decision_job_resume"
        ),
    )
    op.create_index(
        op.f("ix_candidate_decisions_enterprise_job_id"),
        "candidate_decisions",
        ["enterprise_job_id"],
    )
    op.create_index(
        op.f("ix_candidate_decisions_resume_id"),
        "candidate_decisions",
        ["resume_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_candidate_decisions_resume_id"), table_name="candidate_decisions")
    op.drop_index(
        op.f("ix_candidate_decisions_enterprise_job_id"),
        table_name="candidate_decisions",
    )
    op.drop_table("candidate_decisions")
