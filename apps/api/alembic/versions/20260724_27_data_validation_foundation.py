"""add Data Validation domain persistence foundation

Revision ID: 20260724_27
Revises: 20260723_26
"""

from alembic import op
import sqlalchemy as sa


revision = "20260724_27"
down_revision = "20260723_26"
branch_labels = None
depends_on = None


TABLE_COLUMNS = {
    "data_validation_tasks": {
        "id",
        "extraction_task_id",
        "source_jd_version_id",
        "bundle_fingerprint",
        "policy_version",
        "idempotency_key",
        "status",
        "attempt_count",
        "max_attempts",
        "started_at",
        "finished_at",
        "last_error_code",
        "last_error_message",
        "retryable",
        "lock_version",
        "created_at",
        "updated_at",
    },
    "validation_reports": {
        "id",
        "data_validation_task_id",
        "conclusion",
        "idempotency_key",
        "policy_version",
        "report_payload",
        "created_at",
    },
    "validated_bundle_snapshots": {
        "id",
        "validation_report_id",
        "data_validation_task_id",
        "extraction_task_id",
        "source_jd_version_id",
        "validation_conclusion",
        "bundle_fingerprint",
        "idempotency_key",
        "bundle_payload",
        "report_payload",
        "created_at",
    },
}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {
        table for table in TABLE_COLUMNS if inspector.has_table(table)
    }
    if existing:
        if existing != set(TABLE_COLUMNS):
            missing_tables = sorted(set(TABLE_COLUMNS) - existing)
            raise RuntimeError(
                "Existing Data Validation schema is incomplete; "
                f"missing tables: {missing_tables}"
            )
        for table, required in TABLE_COLUMNS.items():
            columns = {
                item["name"] for item in inspector.get_columns(table)
            }
            if "bundle_id" in columns and "bundle_fingerprint" in required:
                required = required - {"bundle_fingerprint"} | {"bundle_id"}
            missing_columns = sorted(required - columns)
            if missing_columns:
                raise RuntimeError(
                    f"Existing {table} table is incomplete; "
                    f"missing columns: {missing_columns}"
                )
        _install_snapshot_immutability()
        _install_sqlite_snapshot_admission()
        return

    op.create_table(
        "data_validation_tasks",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("extraction_task_id", sa.String(36), nullable=False),
        sa.Column("source_jd_version_id", sa.String(36), nullable=False),
        sa.Column("bundle_fingerprint", sa.String(71), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(96), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(80), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column(
            "lock_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status in ('pending', 'running', 'succeeded', 'failed')",
            name="ck_data_validation_tasks_status_allowed",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_data_validation_tasks_attempt_nonnegative",
        ),
        sa.CheckConstraint(
            "max_attempts > 0",
            name="ck_data_validation_tasks_max_attempts_positive",
        ),
        sa.CheckConstraint(
            "attempt_count <= max_attempts",
            name="ck_data_validation_tasks_attempt_within_max",
        ),
        sa.CheckConstraint(
            "lock_version >= 1",
            name="ck_data_validation_tasks_lock_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["extraction_task_id"],
            ["extraction_tasks.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_jd_version_id"],
            ["source_jd_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "extraction_task_id",
            "bundle_fingerprint",
            "policy_version",
            name="uq_data_validation_tasks_natural_key",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_data_validation_tasks_idempotency_key",
        ),
    )
    op.create_index(
        "ix_data_validation_tasks_extraction_task_id",
        "data_validation_tasks",
        ["extraction_task_id"],
    )
    op.create_index(
        "ix_data_validation_tasks_source_jd_version_id",
        "data_validation_tasks",
        ["source_jd_version_id"],
    )
    op.create_index(
        "ix_data_validation_tasks_status",
        "data_validation_tasks",
        ["status"],
    )

    op.create_table(
        "validation_reports",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("data_validation_task_id", sa.String(36), nullable=False),
        sa.Column("conclusion", sa.String(16), nullable=False),
        sa.Column("idempotency_key", sa.String(96), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("report_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "conclusion in ('pass', 'warn', 'block')",
            name="ck_validation_reports_conclusion_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["data_validation_task_id"],
            ["data_validation_tasks.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "data_validation_task_id",
            name="uq_validation_reports_task_id",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_validation_reports_idempotency_key",
        ),
    )
    op.create_index(
        "ix_validation_reports_data_validation_task_id",
        "validation_reports",
        ["data_validation_task_id"],
    )
    op.create_index(
        "ix_validation_reports_conclusion",
        "validation_reports",
        ["conclusion"],
    )

    op.create_table(
        "validated_bundle_snapshots",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("validation_report_id", sa.String(36), nullable=False),
        sa.Column("data_validation_task_id", sa.String(36), nullable=False),
        sa.Column("extraction_task_id", sa.String(36), nullable=False),
        sa.Column("source_jd_version_id", sa.String(36), nullable=False),
        sa.Column("validation_conclusion", sa.String(16), nullable=False),
        sa.Column("bundle_fingerprint", sa.String(71), nullable=False),
        sa.Column("idempotency_key", sa.String(96), nullable=False),
        sa.Column("bundle_payload", sa.JSON(), nullable=False),
        sa.Column("report_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "validation_conclusion in ('pass', 'warn')",
            name="ck_validated_bundle_snapshots_non_blocking",
        ),
        sa.ForeignKeyConstraint(
            ["validation_report_id"],
            ["validation_reports.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["data_validation_task_id"],
            ["data_validation_tasks.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["extraction_task_id"],
            ["extraction_tasks.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_jd_version_id"],
            ["source_jd_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "validation_report_id",
            name="uq_validated_bundle_snapshots_report_id",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_validated_bundle_snapshots_idempotency_key",
        ),
    )
    for column in (
        "validation_report_id",
        "data_validation_task_id",
        "extraction_task_id",
        "source_jd_version_id",
    ):
        op.create_index(
            f"ix_validated_bundle_snapshots_{column}",
            "validated_bundle_snapshots",
            [column],
        )
    _install_snapshot_immutability()
    _install_sqlite_snapshot_admission()


def _install_snapshot_immutability() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute(
            "CREATE TRIGGER IF NOT EXISTS "
            "validated_bundle_snapshots_reject_update "
            "BEFORE UPDATE ON validated_bundle_snapshots BEGIN "
            "SELECT RAISE(ABORT, "
            "'ValidatedBundleSnapshot records are immutable'); END"
        )
        op.execute(
            "CREATE TRIGGER IF NOT EXISTS "
            "validated_bundle_snapshots_reject_delete "
            "BEFORE DELETE ON validated_bundle_snapshots BEGIN "
            "SELECT RAISE(ABORT, "
            "'ValidatedBundleSnapshot records are immutable'); END"
        )
    elif dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_validated_bundle_snapshot_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'ValidatedBundleSnapshot records are immutable'
                    USING ERRCODE = 'integrity_constraint_violation';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            "CREATE TRIGGER validated_bundle_snapshots_reject_mutation "
            "BEFORE UPDATE OR DELETE ON validated_bundle_snapshots "
            "FOR EACH ROW EXECUTE FUNCTION "
            "reject_validated_bundle_snapshot_mutation()"
        )


def _install_sqlite_snapshot_admission() -> None:
    if op.get_bind().dialect.name != "sqlite":
        return
    inspector = sa.inspect(op.get_bind())
    task_columns = {
        item["name"] for item in inspector.get_columns("data_validation_tasks")
    }
    snapshot_columns = {
        item["name"] for item in inspector.get_columns("validated_bundle_snapshots")
    }
    task_bundle_column = "bundle_id" if "bundle_id" in task_columns else "bundle_fingerprint"
    snapshot_bundle_column = (
        "bundle_id" if "bundle_id" in snapshot_columns else "bundle_fingerprint"
    )
    op.execute(
        f"""
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
                  AND task.{task_bundle_column} =
                      NEW.{snapshot_bundle_column}
                  AND extraction.source_jd_version_id =
                      NEW.source_jd_version_id
            ) THEN RAISE(
                ABORT,
                'ValidatedBundleSnapshot admission or lineage is invalid'
            ) END;
        END
        """
    )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute(
            "DROP TRIGGER IF EXISTS "
            "validated_bundle_snapshots_validate_insert"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS "
            "validated_bundle_snapshots_reject_delete"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS "
            "validated_bundle_snapshots_reject_update"
        )
    elif dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS "
            "validated_bundle_snapshots_reject_mutation "
            "ON validated_bundle_snapshots"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS "
            "reject_validated_bundle_snapshot_mutation()"
        )
    for column in (
        "source_jd_version_id",
        "extraction_task_id",
        "data_validation_task_id",
        "validation_report_id",
    ):
        op.drop_index(
            f"ix_validated_bundle_snapshots_{column}",
            table_name="validated_bundle_snapshots",
        )
    op.drop_table("validated_bundle_snapshots")
    op.drop_index(
        "ix_validation_reports_conclusion",
        table_name="validation_reports",
    )
    op.drop_index(
        "ix_validation_reports_data_validation_task_id",
        table_name="validation_reports",
    )
    op.drop_table("validation_reports")
    op.drop_index(
        "ix_data_validation_tasks_status",
        table_name="data_validation_tasks",
    )
    op.drop_index(
        "ix_data_validation_tasks_source_jd_version_id",
        table_name="data_validation_tasks",
    )
    op.drop_index(
        "ix_data_validation_tasks_extraction_task_id",
        table_name="data_validation_tasks",
    )
    op.drop_table("data_validation_tasks")
