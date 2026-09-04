"""widen the build watermark validation policy version column

The domain can emit ``policy-set:<v1>,<v2>`` when one graph build contains
published facts validated under different policy bindings. The original
VARCHAR(100) is too small for that composite value, so store it as text.

Revision ID: 0024_widen_watermark_validation_policy_version
Revises: 0023_backfill_document_source_versions
"""
from alembic import op
import sqlalchemy as sa


revision = "0024_widen_watermark_validation_policy_version"
down_revision = "0023_backfill_document_source_versions"
branch_labels = None
depends_on = None


def _restore_immutability_triggers() -> None:
    for action in ("update", "delete"):
        name = f"trg_build_input_watermarks_reject_{action}"
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
        op.execute(
            f"CREATE TRIGGER {name} BEFORE {action.upper()} "
            "ON build_input_watermarks "
            "BEGIN SELECT RAISE(ABORT, 'build_input_watermarks is immutable'); END"
        )


def upgrade() -> None:
    with op.batch_alter_table("build_input_watermarks") as batch:
        batch.alter_column(
            "validation_policy_version",
            existing_type=sa.String(100),
            type_=sa.Text(),
            existing_nullable=True,
        )
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        _restore_immutability_triggers()


def downgrade() -> None:
    with op.batch_alter_table("build_input_watermarks") as batch:
        batch.alter_column(
            "validation_policy_version",
            existing_type=sa.Text(),
            type_=sa.String(100),
            existing_nullable=True,
        )
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        _restore_immutability_triggers()
