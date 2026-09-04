"""widen validation idempotency keys

Revision ID: 1ce986a44e26
Revises: c25a72547e98
Create Date: 2026-08-07 01:55:23.814803

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1ce986a44e26'
down_revision: Union[str, Sequence[str], None] = 'c25a72547e98'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLES = (
    "data_validation_tasks",
    "validation_reports",
    "validated_bundle_snapshots",
)


def _sqlite_admission_trigger_sql() -> str:
    return """
    CREATE TRIGGER IF NOT EXISTS
    validated_bundle_snapshots_validate_insert
    BEFORE INSERT ON validated_bundle_snapshots
    BEGIN
        SELECT CASE WHEN NOT EXISTS (
            SELECT 1
            FROM validation_reports AS report
            JOIN data_validation_tasks AS task
              ON task.id = report.data_validation_task_id
            JOIN extraction_tasks AS extraction
              ON extraction.id = task.extraction_task_id
            WHERE report.id = NEW.validation_report_id
              AND report.conclusion IN ('pass', 'warn')
              AND report.data_validation_task_id =
                  NEW.data_validation_task_id
              AND report.conclusion = NEW.validation_conclusion
              AND task.status = 'succeeded'
              AND task.extraction_task_id = NEW.extraction_task_id
              AND task.source_jd_version_id =
                  NEW.source_jd_version_id
              AND task.bundle_id =
                  NEW.bundle_id
              AND extraction.source_jd_version_id =
                  NEW.source_jd_version_id
        ) THEN RAISE(
            ABORT,
            'ValidatedBundleSnapshot admission or lineage is invalid'
        ) END;
    END
    """


def upgrade() -> None:
    """Upgrade schema."""
    if op.get_bind().dialect.name == "sqlite":
        # The validated-bundle admission trigger references these tables, so
        # it must be released while the batch recreation renames them.
        op.execute(
            "DROP TRIGGER IF EXISTS validated_bundle_snapshots_validate_insert"
        )
    for table in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                "idempotency_key",
                existing_type=sa.String(96),
                type_=sa.String(180),
                existing_nullable=False,
            )
    if op.get_bind().dialect.name == "sqlite":
        op.execute(_sqlite_admission_trigger_sql())


def downgrade() -> None:
    """Downgrade schema."""
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "DROP TRIGGER IF EXISTS validated_bundle_snapshots_validate_insert"
        )
    for table in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                "idempotency_key",
                existing_type=sa.String(180),
                type_=sa.String(96),
                existing_nullable=False,
            )
    if op.get_bind().dialect.name == "sqlite":
        op.execute(_sqlite_admission_trigger_sql())
