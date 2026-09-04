"""persist explicit JD extraction mode

Revision ID: 20260804_57
Revises: 20260804_56
"""

import sqlalchemy as sa
from alembic import op


revision = "20260804_57"
down_revision = "20260804_56"
branch_labels = None
depends_on = None


def _sqlite_dependent_triggers() -> list[tuple[str, str]]:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return []
    rows = bind.execute(
        sa.text(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'trigger' AND sql LIKE '%extraction_tasks%'"
        )
    ).fetchall()
    return [(str(row[0]), str(row[1])) for row in rows if row[1]]


def _drop_triggers(triggers: list[tuple[str, str]]) -> None:
    bind = op.get_bind()
    quote = bind.dialect.identifier_preparer.quote
    for name, _ in triggers:
        op.execute(sa.text(f"DROP TRIGGER {quote(name)}"))


def _restore_triggers(triggers: list[tuple[str, str]]) -> None:
    for _, sql in triggers:
        op.execute(sa.text(sql))


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = {
        column["name"] for column in sa.inspect(bind).get_columns("extraction_tasks")
    }
    # Legacy development databases may have created the current ORM table via
    # metadata.create_all before Alembic reached this revision.
    if "extraction_mode" in existing_columns:
        return
    op.add_column(
        "extraction_tasks",
        sa.Column("extraction_mode", sa.String(length=16), nullable=True),
    )
    op.execute(
        "UPDATE extraction_tasks SET extraction_mode = 'llm' "
        "WHERE extraction_mode IS NULL"
    )
    triggers = _sqlite_dependent_triggers()
    _drop_triggers(triggers)
    try:
        with op.batch_alter_table("extraction_tasks") as batch_op:
            batch_op.alter_column("extraction_mode", nullable=False)
            batch_op.create_check_constraint(
                "ck_extraction_tasks_mode_allowed",
                "extraction_mode in ('llm', 'rule')",
            )
    finally:
        _restore_triggers(triggers)


def downgrade() -> None:
    triggers = _sqlite_dependent_triggers()
    _drop_triggers(triggers)
    try:
        with op.batch_alter_table("extraction_tasks") as batch_op:
            batch_op.drop_constraint(
                "ck_extraction_tasks_mode_allowed",
                type_="check",
            )
            batch_op.drop_column("extraction_mode")
    finally:
        _restore_triggers(triggers)
