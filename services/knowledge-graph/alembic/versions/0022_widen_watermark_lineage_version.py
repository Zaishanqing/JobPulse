"""widen the build watermark lineage version column

The domain emits ``<catalog_snapshot_id>:<catalog_source_version>`` where the
catalog snapshot id already embeds the source version, so values can exceed
the original VARCHAR(64). The SQLite batch recreate also clears the legacy
server default and restores the table-level immutability triggers that the
recreate would otherwise drop.

Revision ID: 0022_widen_watermark_lineage_version
Revises: 0021_explicit_lineage_versions
"""

from alembic import op
import sqlalchemy as sa


revision = "0022_widen_watermark_lineage_version"
down_revision = "0021_explicit_lineage_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("build_input_watermarks") as batch:
        batch.alter_column(
            "lineage_version",
            existing_type=sa.String(64),
            type_=sa.String(128),
            existing_nullable=False,
            server_default=None,
        )
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # The batch recreate drops the table-level immutability triggers;
        # restore them so the head schema keeps the governance guarantees.
        for action in ("update", "delete"):
            name = f"trg_build_input_watermarks_reject_{action}"
            op.execute(f"DROP TRIGGER IF EXISTS {name}")
            op.execute(
                f"CREATE TRIGGER {name} BEFORE {action.upper()} "
                "ON build_input_watermarks "
                "BEGIN SELECT RAISE(ABORT, 'build_input_watermarks is immutable'); END"
            )


def downgrade() -> None:
    with op.batch_alter_table("build_input_watermarks") as batch:
        batch.alter_column(
            "lineage_version",
            existing_type=sa.String(128),
            type_=sa.String(64),
            existing_nullable=False,
        )
