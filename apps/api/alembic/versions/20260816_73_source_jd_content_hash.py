"""add raw content hash lineage to SourceJDVersion

Revision ID: 20260816_73
Revises: 20260816_72
"""

from collections.abc import Sequence

import hashlib
import sqlalchemy as sa
from alembic import op


revision: str = "20260816_73"
down_revision: str | None = "20260816_72"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _content_hash(raw_text: str) -> str:
    return "sha256:" + hashlib.sha256(raw_text.encode("utf-8")).hexdigest()


def _drop_immutability_triggers(table: str, message: str) -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute(f"DROP TRIGGER IF EXISTS {table}_reject_update")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_reject_delete")
    elif dialect == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS {table}_reject_mutation ON {table}")


def _recreate_immutability_triggers(table: str, message: str) -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute(
            f"CREATE TRIGGER {table}_reject_update "
            f"BEFORE UPDATE ON {table} BEGIN "
            f"SELECT RAISE(ABORT, '{message}'); END"
        )
        op.execute(
            f"CREATE TRIGGER {table}_reject_delete "
            f"BEFORE DELETE ON {table} BEGIN "
            f"SELECT RAISE(ABORT, '{message}'); END"
        )
    elif dialect == "postgresql":
        op.execute(
            f"CREATE TRIGGER {table}_reject_mutation "
            f"BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION reject_source_jd_version_mutation()"
        )


def upgrade() -> None:
    _drop_immutability_triggers("source_jd_versions", "SourceJDVersion records are immutable")
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("source_jd_versions")}
    with op.batch_alter_table("source_jd_versions") as batch_op:
        if "content_hash" not in columns:
            batch_op.add_column(
                sa.Column("content_hash", sa.String(length=71), nullable=True)
            )

    source_jd_versions = sa.table(
        "source_jd_versions",
        sa.column("id", sa.String),
        sa.column("raw_text", sa.Text),
        sa.column("content_hash", sa.String),
    )
    rows = bind.execute(
        sa.select(
            source_jd_versions.c.id,
            source_jd_versions.c.raw_text,
        ).where(source_jd_versions.c.content_hash.is_(None))
    ).fetchall()
    for row in rows:
        bind.execute(
            source_jd_versions.update()
            .where(source_jd_versions.c.id == row.id)
            .values(content_hash=_content_hash(row.raw_text))
        )

    with op.batch_alter_table("source_jd_versions") as batch_op:
        batch_op.alter_column("content_hash", nullable=False)
    _recreate_immutability_triggers(
        "source_jd_versions", "SourceJDVersion records are immutable"
    )


def downgrade() -> None:
    _drop_immutability_triggers("source_jd_versions", "SourceJDVersion records are immutable")
    with op.batch_alter_table("source_jd_versions") as batch_op:
        batch_op.drop_column("content_hash")
    _recreate_immutability_triggers(
        "source_jd_versions", "SourceJDVersion records are immutable"
    )
