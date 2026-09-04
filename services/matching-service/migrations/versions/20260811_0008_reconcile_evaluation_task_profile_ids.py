"""Reconcile legacy evaluation task fingerprint columns with profile IDs.

Revision ID: 20260811_0008
Revises: 20260805_0007

Some deployed databases predate the current initial migration and contain
``cv_profile_fingerprint`` / ``position_profile_fingerprint`` while Alembic is
already stamped at head.  Add and backfill the contract fields required by the
current task model without dropping the legacy audit columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260811_0008"
down_revision: str | None = "20260805_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {item["name"] for item in inspector.get_columns("evaluation_tasks")}


def upgrade() -> None:
    columns = _columns()
    added_cv = "cv_profile_id" not in columns
    added_position = "position_profile_id" not in columns
    if added_cv:
        op.add_column(
            "evaluation_tasks",
            sa.Column("cv_profile_id", sa.String(length=200), nullable=True),
        )
    if added_position:
        op.add_column(
            "evaluation_tasks",
            sa.Column("position_profile_id", sa.String(length=200), nullable=True),
        )
    if not (added_cv or added_position):
        return

    columns = _columns()
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        cv_json = "NULLIF(cv_profile_json->>'profile_id', '')"
        position_json = "NULLIF(position_profile_json->>'profile_id', '')"
    elif dialect == "sqlite":
        cv_json = "NULLIF(json_extract(cv_profile_json, '$.profile_id'), '')"
        position_json = "NULLIF(json_extract(position_profile_json, '$.profile_id'), '')"
    else:
        raise RuntimeError(f"Unsupported matching migration dialect: {dialect}")

    if added_cv:
        legacy_cv = (
            "cv_profile_fingerprint"
            if "cv_profile_fingerprint" in columns
            else "NULL"
        )
        op.execute(
            sa.text(
                "UPDATE evaluation_tasks SET cv_profile_id = "
                f"COALESCE({cv_json}, {legacy_cv}, 'legacy-cv:' || task_id) "
                "WHERE cv_profile_id IS NULL"
            )
        )
        op.alter_column(
            "evaluation_tasks",
            "cv_profile_id",
            existing_type=sa.String(length=200),
            nullable=False,
        )
    if added_position:
        legacy_position = (
            "position_profile_fingerprint"
            if "position_profile_fingerprint" in columns
            else "NULL"
        )
        op.execute(
            sa.text(
                "UPDATE evaluation_tasks SET position_profile_id = "
                f"COALESCE({position_json}, {legacy_position}, 'legacy-position:' || task_id) "
                "WHERE position_profile_id IS NULL"
            )
        )
        op.alter_column(
            "evaluation_tasks",
            "position_profile_id",
            existing_type=sa.String(length=200),
            nullable=False,
        )


def downgrade() -> None:
    # These columns may have originated in revision 0001 on clean databases;
    # provenance cannot be inferred during downgrade. Preserve them to avoid
    # destructive schema loss when rolling back only this compatibility step.
    pass
