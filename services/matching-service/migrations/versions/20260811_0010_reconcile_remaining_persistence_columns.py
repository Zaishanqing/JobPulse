"""Reconcile the remaining legacy matching persistence columns.

Revision ID: 20260811_0010
Revises: 20260811_0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260811_0010"
down_revision: str | None = "20260811_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table_name: str) -> dict[str, dict[str, object]]:
    return {
        item["name"]: item
        for item in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _relax_legacy_input_fingerprint(table_name: str) -> None:
    columns = _columns(table_name)
    column = columns.get("input_fingerprint")
    if column is not None and not bool(column["nullable"]):
        op.alter_column(
            table_name,
            "input_fingerprint",
            existing_type=column["type"],
            nullable=True,
        )


def _add_task_idempotency(table_name: str, row_id_column: str) -> None:
    if "idempotency_key" in _columns(table_name):
        return
    op.add_column(
        table_name,
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
    )
    op.execute(
        sa.text(
            f"UPDATE {table_name} AS target SET idempotency_key = COALESCE("
            "(SELECT task.idempotency_key FROM evaluation_tasks AS task "
            "WHERE task.access_scope = target.access_scope "
            "AND task.task_id = target.task_id), "
            f"substr('legacy:' || target.{row_id_column}, 1, 200)) "
            "WHERE target.idempotency_key IS NULL"
        )
    )
    op.alter_column(
        table_name,
        "idempotency_key",
        existing_type=sa.String(length=200),
        nullable=False,
    )


def _reconcile_vector_profile_version() -> None:
    columns = _columns("vector_index_references")
    added = "profile_version" not in columns
    if added:
        op.add_column(
            "vector_index_references",
            sa.Column("profile_version", sa.String(length=200), nullable=True),
        )
        legacy = "profile_fingerprint" if "profile_fingerprint" in columns else "NULL"
        op.execute(
            sa.text(
                "UPDATE vector_index_references SET profile_version = "
                f"COALESCE({legacy}, substr('legacy:' || reference_id, 1, 200)) "
                "WHERE profile_version IS NULL"
            )
        )
        op.alter_column(
            "vector_index_references",
            "profile_version",
            existing_type=sa.String(length=200),
            nullable=False,
        )
    columns = _columns("vector_index_references")
    legacy_column = columns.get("profile_fingerprint")
    if legacy_column is not None and not bool(legacy_column["nullable"]):
        op.alter_column(
            "vector_index_references",
            "profile_fingerprint",
            existing_type=legacy_column["type"],
            nullable=True,
        )
    if added:
        constraints = {
            item["name"]
            for item in sa.inspect(op.get_bind()).get_unique_constraints(
                "vector_index_references"
            )
        }
        if "uq_vector_index_reference_lineage" in constraints:
            op.drop_constraint(
                "uq_vector_index_reference_lineage",
                "vector_index_references",
                type_="unique",
            )
        op.create_unique_constraint(
            "uq_vector_index_reference_lineage",
            "vector_index_references",
            [
                "tenant_ref",
                "entity_type",
                "entity_id",
                "fragment_id",
                "profile_version",
                "embedding_revision",
                "grant_id",
                "grant_version",
            ],
        )


def upgrade() -> None:
    _add_task_idempotency("audit_records", "audit_id")
    _relax_legacy_input_fingerprint("audit_records")
    _add_task_idempotency("persisted_evaluations", "evaluation_id")
    _relax_legacy_input_fingerprint("persisted_evaluations")
    _reconcile_vector_profile_version()


def downgrade() -> None:
    # The reconciled columns are part of the current contract and may already
    # have existed on clean databases. Preserve them on a one-step rollback.
    pass
