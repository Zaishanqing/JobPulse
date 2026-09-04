"""add CV source, extraction validation, snapshot, and resume lineage

Revision ID: 20260725_28
Revises: 20260724_27
"""

from alembic import op
import sqlalchemy as sa


revision = "20260725_28"
down_revision = "20260724_27"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "source_cvs" not in tables:
        op.create_table(
        "source_cvs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_platform", sa.String(64), nullable=False),
        sa.Column("source_record_id", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "owner_id", "source_platform", "source_record_id",
            name="uq_source_cvs_owner_source_identity",
        ),
        )
    if "source_cv_versions" not in tables:
        op.create_table(
        "source_cv_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_cv_id", sa.String(36), sa.ForeignKey("source_cvs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(71), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "source_cv_id", "content_hash", name="uq_source_cv_versions_content"
        ),
        )
    resume_columns = {
        item["name"] for item in sa.inspect(bind).get_columns("resumes")
    }
    if "source_cv_version_id" not in resume_columns:
        with op.batch_alter_table("resumes") as batch_op:
            batch_op.add_column(
                sa.Column("source_cv_version_id", sa.String(36), nullable=True)
            )
            batch_op.create_foreign_key(
                "fk_resumes_source_cv_version_id",
                "source_cv_versions",
                ["source_cv_version_id"],
                ["id"],
                ondelete="RESTRICT",
            )
            batch_op.create_index(
                "ix_resumes_source_cv_version_id",
                ["source_cv_version_id"],
                unique=True,
            )
    if "cv_extraction_tasks" not in tables:
        op.create_table(
        "cv_extraction_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_cv_version_id", sa.String(36), sa.ForeignKey("source_cv_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("request_fingerprint", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("last_error_code", sa.String(96), nullable=True),
        sa.Column("last_error_message", sa.String(512), nullable=True),
        sa.Column("validation_conclusion", sa.String(16), nullable=True),
        sa.Column("validation_report_payload", sa.JSON(), nullable=True),
        sa.Column("resume_id", sa.String(36), sa.ForeignKey("resumes.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "source_cv_version_id", "request_fingerprint",
            name="uq_cv_extraction_tasks_natural_key",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="ck_cv_extraction_tasks_status",
        ),
        sa.CheckConstraint(
            "validation_conclusion IS NULL OR validation_conclusion IN ('pass', 'warn', 'block')",
            name="ck_cv_extraction_tasks_conclusion",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_cv_extraction_tasks_attempts"),
        sa.CheckConstraint(
            "max_attempts > 0 AND attempt_count <= max_attempts",
            name="ck_cv_extraction_tasks_attempt_limit",
        ),
        )
        op.create_index(
            "ix_cv_extraction_tasks_source_cv_version_id",
            "cv_extraction_tasks",
            ["source_cv_version_id"],
        )
        op.create_index(
            "ix_cv_extraction_tasks_status", "cv_extraction_tasks", ["status"]
        )
    if "validated_cv_snapshots" not in tables:
        op.create_table(
        "validated_cv_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("cv_extraction_task_id", sa.String(36), sa.ForeignKey("cv_extraction_tasks.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_cv_version_id", sa.String(36), sa.ForeignKey("source_cv_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("conclusion", sa.String(16), nullable=False),
        sa.Column("content_fingerprint", sa.String(71), nullable=False),
        sa.Column("extraction_payload", sa.JSON(), nullable=False),
        sa.Column("normalized_payload", sa.JSON(), nullable=False),
        sa.Column("findings_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("cv_extraction_task_id", name="uq_validated_cv_snapshots_task"),
        sa.CheckConstraint("conclusion IN ('pass', 'warn')", name="ck_validated_cv_snapshots_conclusion"),
        )
        op.create_index(
            "ix_validated_cv_snapshots_cv_extraction_task_id",
            "validated_cv_snapshots",
            ["cv_extraction_task_id"],
        )
    _install_immutability()


def _install_immutability() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for table in ("source_cv_versions", "validated_cv_snapshots"):
            op.execute(
                f"CREATE TRIGGER IF NOT EXISTS {table}_reject_update BEFORE UPDATE ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{table} records are immutable'); END"
            )
            op.execute(
                f"CREATE TRIGGER IF NOT EXISTS {table}_reject_delete BEFORE DELETE ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{table} records are immutable'); END"
            )
    elif dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_cv_immutable_mutation() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'CV source versions and snapshots are immutable'
                    USING ERRCODE = 'integrity_constraint_violation';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        for table in ("source_cv_versions", "validated_cv_snapshots"):
            op.execute(
                f"CREATE TRIGGER {table}_reject_mutation BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION reject_cv_immutable_mutation()"
            )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for table in ("source_cv_versions", "validated_cv_snapshots"):
            op.execute(f"DROP TRIGGER IF EXISTS {table}_reject_delete")
            op.execute(f"DROP TRIGGER IF EXISTS {table}_reject_update")
    elif dialect == "postgresql":
        for table in ("source_cv_versions", "validated_cv_snapshots"):
            op.execute(f"DROP TRIGGER IF EXISTS {table}_reject_mutation ON {table}")
        op.execute("DROP FUNCTION IF EXISTS reject_cv_immutable_mutation()")
    op.drop_index("ix_validated_cv_snapshots_cv_extraction_task_id", table_name="validated_cv_snapshots")
    op.drop_table("validated_cv_snapshots")
    op.drop_index("ix_cv_extraction_tasks_status", table_name="cv_extraction_tasks")
    op.drop_index("ix_cv_extraction_tasks_source_cv_version_id", table_name="cv_extraction_tasks")
    op.drop_table("cv_extraction_tasks")
    with op.batch_alter_table("resumes") as batch_op:
        batch_op.drop_index("ix_resumes_source_cv_version_id")
        batch_op.drop_constraint("fk_resumes_source_cv_version_id", type_="foreignkey")
        batch_op.drop_column("source_cv_version_id")
    op.drop_table("source_cv_versions")
    op.drop_table("source_cvs")
