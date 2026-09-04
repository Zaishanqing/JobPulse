"""add candidate submissions

Revision ID: 20260712_10
Revises: 20260712_09
Create Date: 2026-07-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260712_10"
down_revision: Union[str, Sequence[str], None] = "20260712_09"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "candidate_submissions" in inspector.get_table_names():
        return
    op.create_table(
        "candidate_submissions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("resume_id", sa.String(length=36), nullable=False),
        sa.Column("enterprise_job_id", sa.String(length=36), nullable=False),
        sa.Column("enterprise_id", sa.String(length=36), nullable=False),
        sa.Column("resume_owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status in ('submitted', 'revoked')",
            name="ck_candidate_submissions_status_allowed",
        ),
        sa.ForeignKeyConstraint(["enterprise_id"], ["enterprises.id"]),
        sa.ForeignKeyConstraint(["enterprise_job_id"], ["enterprise_jobs.id"]),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"]),
        sa.ForeignKeyConstraint(["resume_owner_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "enterprise_job_id", "resume_id", name="uq_candidate_submission_job_resume"
        ),
    )
    for column in (
        "resume_id",
        "enterprise_job_id",
        "enterprise_id",
        "resume_owner_user_id",
    ):
        op.create_index(op.f(f"ix_candidate_submissions_{column}"), "candidate_submissions", [column])


def downgrade() -> None:
    if "candidate_submissions" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("candidate_submissions")
