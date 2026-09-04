"""close review authority and CV worker/validation lineage

Revision ID: 20260725_29
Revises: 20260725_28
"""

from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

from alembic import op
import sqlalchemy as sa


revision = "20260725_29"
down_revision = "20260725_28"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _column_names(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _drop_cv_snapshot_immutability() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS validated_cv_snapshots_reject_update")
        op.execute("DROP TRIGGER IF EXISTS validated_cv_snapshots_reject_delete")
    elif dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS validated_cv_snapshots_reject_mutation "
            "ON validated_cv_snapshots"
        )


def _install_cv_snapshot_immutability() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute(
            "CREATE TRIGGER validated_cv_snapshots_reject_update "
            "BEFORE UPDATE ON validated_cv_snapshots "
            "BEGIN SELECT RAISE(ABORT, 'validated_cv_snapshots records are immutable'); END"
        )
        op.execute(
            "CREATE TRIGGER validated_cv_snapshots_reject_delete "
            "BEFORE DELETE ON validated_cv_snapshots "
            "BEGIN SELECT RAISE(ABORT, 'validated_cv_snapshots records are immutable'); END"
        )
    elif dialect == "postgresql":
        op.execute(
            "CREATE TRIGGER validated_cv_snapshots_reject_mutation "
            "BEFORE UPDATE OR DELETE ON validated_cv_snapshots FOR EACH ROW "
            "EXECUTE FUNCTION reject_cv_immutable_mutation()"
        )


def upgrade() -> None:
    bind = op.get_bind()
    duplicate = bind.execute(
        sa.text(
            "SELECT object_type, object_id, COUNT(*) AS count "
            "FROM review_tasks WHERE status IN ('pending', 'claimed') "
            "GROUP BY object_type, object_id HAVING COUNT(*) > 1"
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "Multiple active review tasks must be resolved before migration: "
            f"{duplicate.object_type}/{duplicate.object_id}"
        )
    if "uq_review_tasks_active_object" not in _index_names("review_tasks"):
        op.create_index(
            "uq_review_tasks_active_object",
            "review_tasks",
            ["object_type", "object_id"],
            unique=True,
            sqlite_where=sa.text("status IN ('pending', 'claimed')"),
            postgresql_where=sa.text("status IN ('pending', 'claimed')"),
        )

    cv_columns = _column_names("cv_extraction_tasks")
    cv_indexes = _index_names("cv_extraction_tasks")
    cv_column_definitions = (
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("claimed_by", sa.String(120), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    with op.batch_alter_table("cv_extraction_tasks") as batch_op:
        for column in cv_column_definitions:
            if column.name not in cv_columns:
                batch_op.add_column(column)
        for index_name, columns in (
            ("ix_cv_extraction_tasks_claimed_by", ["claimed_by"]),
            ("ix_cv_extraction_tasks_lease_expires_at", ["lease_expires_at"]),
            ("ix_cv_extraction_tasks_next_attempt_at", ["next_attempt_at"]),
            (
                "ix_cv_extraction_tasks_claim_queue",
                ["status", "retryable", "next_attempt_at", "created_at"],
            ),
        ):
            if index_name not in cv_indexes:
                batch_op.create_index(index_name, columns)

    if "cv_data_validation_tasks" not in _table_names():
        op.create_table(
            "cv_data_validation_tasks",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "cv_extraction_task_id",
                sa.String(36),
                sa.ForeignKey("cv_extraction_tasks.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column(
                "source_cv_version_id",
                sa.String(36),
                sa.ForeignKey("source_cv_versions.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("policy_version", sa.String(64), nullable=False),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "cv_extraction_task_id",
                "policy_version",
                name="uq_cv_data_validation_tasks_natural_key",
            ),
            sa.CheckConstraint(
                "status in ('succeeded', 'failed')",
                name="ck_cv_data_validation_tasks_status_allowed",
            ),
        )
        op.create_index(
            "ix_cv_data_validation_tasks_cv_extraction_task_id",
            "cv_data_validation_tasks",
            ["cv_extraction_task_id"],
        )
        op.create_index(
            "ix_cv_data_validation_tasks_source_cv_version_id",
            "cv_data_validation_tasks",
            ["source_cv_version_id"],
        )
    if "cv_validation_reports" not in _table_names():
        op.create_table(
            "cv_validation_reports",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "cv_data_validation_task_id",
                sa.String(36),
                sa.ForeignKey("cv_data_validation_tasks.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("conclusion", sa.String(16), nullable=False),
            sa.Column("policy_version", sa.String(64), nullable=False),
            sa.Column("report_payload", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "cv_data_validation_task_id", name="uq_cv_validation_reports_task_id"
            ),
            sa.CheckConstraint(
                "conclusion in ('pass', 'warn', 'block')",
                name="ck_cv_validation_reports_conclusion_allowed",
            ),
        )
        op.create_index(
            "ix_cv_validation_reports_cv_data_validation_task_id",
            "cv_validation_reports",
            ["cv_data_validation_task_id"],
        )
        op.create_index(
            "ix_cv_validation_reports_conclusion", "cv_validation_reports", ["conclusion"]
        )

    snapshot_column_added = "validation_report_id" not in _column_names("validated_cv_snapshots")
    if snapshot_column_added:
        _drop_cv_snapshot_immutability()
        with op.batch_alter_table("validated_cv_snapshots") as batch_op:
            batch_op.add_column(sa.Column("validation_report_id", sa.String(36), nullable=True))
        snapshots = (
            bind.execute(
                sa.text(
                    "SELECT s.id, s.cv_extraction_task_id, s.source_cv_version_id, "
                    "s.policy_version, s.conclusion, s.created_at, t.validation_report_payload "
                    "FROM validated_cv_snapshots s JOIN cv_extraction_tasks t "
                    "ON t.id = s.cv_extraction_task_id"
                )
            )
            .mappings()
            .all()
        )
        for snapshot in snapshots:
            task_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"cv-validation-task:{snapshot['cv_extraction_task_id']}:{snapshot['policy_version']}",
                )
            )
            report_id = str(uuid5(NAMESPACE_URL, f"cv-validation-report:{task_id}"))
            created_at = snapshot["created_at"] or datetime.now(timezone.utc)
            bind.execute(
                sa.text(
                    "INSERT INTO cv_data_validation_tasks "
                    "(id, cv_extraction_task_id, source_cv_version_id, policy_version, status, created_at, finished_at) "
                    "VALUES (:id, :extraction, :source, :policy, 'succeeded', :created, :created)"
                ),
                {
                    "id": task_id,
                    "extraction": snapshot["cv_extraction_task_id"],
                    "source": snapshot["source_cv_version_id"],
                    "policy": snapshot["policy_version"],
                    "created": created_at,
                },
            )
            report_insert = sa.text(
                "INSERT INTO cv_validation_reports "
                "(id, cv_data_validation_task_id, conclusion, policy_version, report_payload, created_at) "
                "VALUES (:id, :task, :conclusion, :policy, :payload, :created)"
            ).bindparams(sa.bindparam("payload", type_=sa.JSON()))
            bind.execute(
                report_insert,
                {
                    "id": report_id,
                    "task": task_id,
                    "conclusion": snapshot["conclusion"],
                    "policy": snapshot["policy_version"],
                    "payload": snapshot["validation_report_payload"] or {},
                    "created": created_at,
                },
            )
            bind.execute(
                sa.text(
                    "UPDATE validated_cv_snapshots SET validation_report_id=:report WHERE id=:id"
                ),
                {"report": report_id, "id": snapshot["id"]},
            )
        with op.batch_alter_table("validated_cv_snapshots") as batch_op:
            batch_op.alter_column(
                "validation_report_id", existing_type=sa.String(36), nullable=False
            )
            batch_op.create_foreign_key(
                "fk_validated_cv_snapshots_validation_report_id",
                "cv_validation_reports",
                ["validation_report_id"],
                ["id"],
                ondelete="RESTRICT",
            )
            batch_op.create_unique_constraint(
                "uq_validated_cv_snapshots_validation_report_id", ["validation_report_id"]
            )
        _install_cv_snapshot_immutability()


def downgrade() -> None:
    _drop_cv_snapshot_immutability()
    with op.batch_alter_table("validated_cv_snapshots") as batch_op:
        batch_op.drop_constraint("uq_validated_cv_snapshots_validation_report_id", type_="unique")
        batch_op.drop_constraint(
            "fk_validated_cv_snapshots_validation_report_id", type_="foreignkey"
        )
        batch_op.drop_column("validation_report_id")
    _install_cv_snapshot_immutability()
    op.drop_index("ix_cv_validation_reports_conclusion", table_name="cv_validation_reports")
    op.drop_index(
        "ix_cv_validation_reports_cv_data_validation_task_id", table_name="cv_validation_reports"
    )
    op.drop_table("cv_validation_reports")
    op.drop_index(
        "ix_cv_data_validation_tasks_source_cv_version_id", table_name="cv_data_validation_tasks"
    )
    op.drop_index(
        "ix_cv_data_validation_tasks_cv_extraction_task_id", table_name="cv_data_validation_tasks"
    )
    op.drop_table("cv_data_validation_tasks")
    with op.batch_alter_table("cv_extraction_tasks") as batch_op:
        batch_op.drop_index("ix_cv_extraction_tasks_claim_queue")
        batch_op.drop_index("ix_cv_extraction_tasks_next_attempt_at")
        batch_op.drop_index("ix_cv_extraction_tasks_lease_expires_at")
        batch_op.drop_index("ix_cv_extraction_tasks_claimed_by")
        batch_op.drop_column("finished_at")
        batch_op.drop_column("next_attempt_at")
        batch_op.drop_column("heartbeat_at")
        batch_op.drop_column("lease_expires_at")
        batch_op.drop_column("claimed_by")
        batch_op.drop_column("retryable")
    op.drop_index("uq_review_tasks_active_object", table_name="review_tasks")
