"""Create evaluation task persistence tables.

Revision ID: 20260727_0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260727_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

contract_json = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "evaluation_tasks",
        sa.Column("access_scope", sa.String(length=200), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("cv_profile_id", sa.String(length=200), nullable=False),
        sa.Column("position_profile_id", sa.String(length=200), nullable=False),
        sa.Column("versions_json", contract_json, nullable=False),
        sa.Column("version_signature", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("evaluation_id", sa.String(length=1024), nullable=True),
        sa.Column("error_code", sa.String(length=200), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cv_profile_json", contract_json, nullable=False),
        sa.Column("position_profile_json", contract_json, nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="ck_evaluation_tasks_status",
        ),
        sa.CheckConstraint(
            "attempt >= 0 AND attempt <= max_attempts", name="ck_task_attempt"
        ),
        sa.PrimaryKeyConstraint("access_scope", "task_id"),
        sa.UniqueConstraint(
            "access_scope",
            "idempotency_key",
            "version_signature",
            name="uq_evaluation_tasks_idempotency",
        ),
    )
    op.create_table(
        "persisted_evaluations",
        sa.Column("access_scope", sa.String(length=200), nullable=False),
        sa.Column("evaluation_id", sa.String(length=1024), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("versions_json", contract_json, nullable=False),
        sa.Column("version_signature", sa.String(length=500), nullable=False),
        sa.Column("evaluation_json", contract_json, nullable=False),
        sa.Column("gap_analysis_json", contract_json, nullable=False),
        sa.Column("stale", sa.Boolean(), nullable=False),
        sa.Column("stale_reason_codes", contract_json, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["access_scope", "task_id"],
            ["evaluation_tasks.access_scope", "evaluation_tasks.task_id"],
            name="fk_persisted_evaluations_task",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("access_scope", "evaluation_id"),
    )
    op.create_index(
        "ix_persisted_evaluations_scope_stale",
        "persisted_evaluations",
        ["access_scope", "stale"],
    )
    op.create_table(
        "audit_records",
        sa.Column("access_scope", sa.String(length=200), nullable=False),
        sa.Column("audit_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.String(length=200), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("algorithm_version", sa.String(length=500), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["access_scope", "task_id"],
            ["evaluation_tasks.access_scope", "evaluation_tasks.task_id"],
            name="fk_audit_records_task",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("access_scope", "audit_id"),
    )
    op.create_index(
        "ix_audit_records_scope_task_time",
        "audit_records",
        ["access_scope", "task_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_records_scope_task_time", table_name="audit_records")
    op.drop_table("audit_records")
    op.drop_index(
        "ix_persisted_evaluations_scope_stale",
        table_name="persisted_evaluations",
    )
    op.drop_table("persisted_evaluations")
    op.drop_table("evaluation_tasks")
