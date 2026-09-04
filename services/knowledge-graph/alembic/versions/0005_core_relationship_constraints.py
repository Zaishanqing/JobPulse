"""add stable core relationship constraints

Revision ID: 0005_core_relationship_constraints
Revises: 0004_graph_version_database_immutability
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_core_relationship_constraints"
down_revision = "0004_graph_version_database_immutability"
branch_labels = None
depends_on = None


def _foreign_keys(table: str) -> set[tuple[str, ...]]:
    return {
        tuple(item["constrained_columns"])
        for item in sa.inspect(op.get_bind()).get_foreign_keys(table)
    }


def _indexes(table: str) -> set[tuple[str, ...]]:
    return {
        tuple(item["column_names"])
        for item in sa.inspect(op.get_bind()).get_indexes(table)
    }


def _named_foreign_keys(table: str) -> set[str]:
    return {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_foreign_keys(table)
        if item.get("name")
    }


def _named_indexes(table: str) -> set[str]:
    return {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_indexes(table)
        if item.get("name")
    }


def upgrade():
    specifications = {
        "review_task_events": [
            (("actor_id",), "fk_review_event_actor", "users", ["actor_id"], ["id"]),
        ],
    }
    for table, values in specifications.items():
        existing = _foreign_keys(table)
        with op.batch_alter_table(table) as batch:
            for columns, name, target, local, remote in values:
                if columns not in existing:
                    batch.create_foreign_key(name, target, local, remote)
    for table, column, name in (
        ("standard_positions", "category_code", "ix_standard_positions_category_code"),
        ("skills", "category_code", "ix_skills_category_code"),
        ("normalized_skill_records", "category_code", "ix_normalized_skill_records_category_code"),
    ):
        if (column,) not in _indexes(table):
            op.create_index(name, table, [column])


def downgrade():
    # Reverse only objects with names owned by this migration. Equivalent
    # constraints/indexes from a historical schema are deliberately preserved.
    if "fk_review_event_actor" in _named_foreign_keys("review_task_events"):
        with op.batch_alter_table("review_task_events") as batch:
            batch.drop_constraint("fk_review_event_actor", type_="foreignkey")
    for table, name in (
        ("standard_positions", "ix_standard_positions_category_code"),
        ("skills", "ix_skills_category_code"),
        ("normalized_skill_records", "ix_normalized_skill_records_category_code"),
    ):
        if name in _named_indexes(table):
            op.drop_index(name, table_name=table)
