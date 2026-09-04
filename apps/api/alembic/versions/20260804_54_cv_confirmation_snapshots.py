"""separate worker success from user confirmation and version snapshots

Revision ID: 20260804_54
Revises: 20260804_55
"""

from alembic import op
import sqlalchemy as sa


revision = "20260804_54"
down_revision = "20260804_55"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    task_columns = {
        column["name"] for column in inspector.get_columns("cv_extraction_tasks")
    }
    task_additions = [
        ("review_payload", sa.Column("review_payload", sa.JSON(), nullable=True)),
        ("review_fingerprint", sa.Column("review_fingerprint", sa.String(length=71), nullable=True)),
        ("confirmation_status", sa.Column("confirmation_status", sa.String(length=16), nullable=True)),
        (
            "latest_validated_cv_snapshot_id",
            sa.Column(
                "latest_validated_cv_snapshot_id",
                sa.String(length=36),
                nullable=True,
            ),
        ),
        ("confirmed_at", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True)),
        ("confirmed_by", sa.Column("confirmed_by", sa.String(length=36), nullable=True)),
        ("review_revision", sa.Column("review_revision", sa.Integer(), nullable=False, server_default="0")),
        (
            "confirmation_idempotency_key",
            sa.Column("confirmation_idempotency_key", sa.String(length=128), nullable=True),
        ),
        (
            "confirmation_idempotency_fingerprint",
            sa.Column(
                "confirmation_idempotency_fingerprint",
                sa.String(length=71),
                nullable=True,
            ),
        ),
    ]
    with op.batch_alter_table("cv_extraction_tasks") as batch_op:
        for name, column in task_additions:
            if name not in task_columns:
                batch_op.add_column(column)
        if "latest_validated_cv_snapshot_id" not in task_columns:
            batch_op.create_foreign_key(
                "fk_cv_extraction_tasks_latest_snapshot",
                "validated_cv_snapshots",
                ["latest_validated_cv_snapshot_id"],
                ["id"],
                ondelete="RESTRICT",
            )

    snapshot_columns = {
        column["name"] for column in inspector.get_columns("validated_cv_snapshots")
    }
    snapshot_additions = [
        ("source_file_id", sa.Column("source_file_id", sa.String(length=36), nullable=True)),
        ("source_file_sha256", sa.Column("source_file_sha256", sa.String(length=71), nullable=True)),
        ("raw_text_sha256", sa.Column("raw_text_sha256", sa.String(length=71), nullable=True)),
        (
            "snapshot_revision",
            sa.Column("snapshot_revision", sa.Integer(), nullable=False, server_default="1"),
        ),
        (
            "supersedes_snapshot_id",
            sa.Column(
                "supersedes_snapshot_id",
                sa.String(length=36),
                sa.ForeignKey(
                    "validated_cv_snapshots.id",
                    ondelete="RESTRICT",
                    name="fk_validated_cv_snapshots_supersedes",
                ),
                nullable=True,
            ),
        ),
        ("confirmed_by", sa.Column("confirmed_by", sa.String(length=36), nullable=True)),
        ("confirmed_at", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True)),
        ("extraction_provider", sa.Column("extraction_provider", sa.String(length=64), nullable=True)),
        ("model", sa.Column("model", sa.String(length=64), nullable=True)),
        ("prompt_version", sa.Column("prompt_version", sa.String(length=64), nullable=True)),
        (
            "extraction_schema_version",
            sa.Column("extraction_schema_version", sa.String(length=64), nullable=True),
        ),
        (
            "normalization_version",
            sa.Column("normalization_version", sa.String(length=64), nullable=True),
        ),
        ("taxonomy_version", sa.Column("taxonomy_version", sa.String(length=71), nullable=True)),
        ("field_decisions", sa.Column("field_decisions", sa.JSON(), nullable=True)),
        ("evidence_payload", sa.Column("evidence_payload", sa.JSON(), nullable=True)),
    ]
    snapshot_unique = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("validated_cv_snapshots")
    }
    snapshot_indexes = {
        index["name"] for index in inspector.get_indexes("validated_cv_snapshots")
    }
    with op.batch_alter_table("validated_cv_snapshots") as batch_op:
        for name, column in snapshot_additions:
            if name not in snapshot_columns:
                batch_op.add_column(column)
        if "uq_validated_cv_snapshots_task" in snapshot_unique:
            batch_op.drop_constraint(
                "uq_validated_cv_snapshots_task", type_="unique"
            )
        if "uq_validated_cv_snapshots_validation_report_id" in snapshot_unique:
            batch_op.drop_constraint(
                "uq_validated_cv_snapshots_validation_report_id", type_="unique"
            )
        if "ix_validated_cv_snapshots_task" not in snapshot_indexes:
            batch_op.create_index(
                "ix_validated_cv_snapshots_task",
                ["cv_extraction_task_id"],
                unique=False,
            )

    submission_columns = {
        column["name"] for column in inspector.get_columns("candidate_submissions")
    }
    submission_indexes = {
        index["name"] for index in inspector.get_indexes("candidate_submissions")
    }
    with op.batch_alter_table("candidate_submissions") as batch_op:
        if "validated_cv_snapshot_id" not in submission_columns:
            batch_op.add_column(
                sa.Column(
                    "validated_cv_snapshot_id",
                    sa.String(length=36),
                    sa.ForeignKey(
                        "validated_cv_snapshots.id",
                        ondelete="RESTRICT",
                        name="fk_candidate_submissions_validated_snapshot",
                    ),
                    nullable=True,
                )
            )
        if "ix_candidate_submissions_validated_cv_snapshot_id" not in submission_indexes:
            batch_op.create_index(
                "ix_candidate_submissions_validated_cv_snapshot_id",
                ["validated_cv_snapshot_id"],
                unique=False,
            )

    op.execute(
        "UPDATE validated_cv_snapshots SET snapshot_revision = 1 "
        "WHERE snapshot_revision IS NULL OR snapshot_revision = 0"
    )
    op.execute(
        "UPDATE validated_cv_snapshots SET confirmed_at = created_at "
        "WHERE confirmed_at IS NULL"
    )
    op.execute(
        "UPDATE candidate_submissions SET validated_cv_snapshot_id = "
        "(SELECT resumes.validated_cv_snapshot_id FROM resumes "
        "WHERE resumes.id = candidate_submissions.resume_id) "
        "WHERE validated_cv_snapshot_id IS NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("candidate_submissions") as batch_op:
        batch_op.drop_index(
            "ix_candidate_submissions_validated_cv_snapshot_id",
            if_exists=True,
        )
        batch_op.drop_constraint(
            "fk_candidate_submissions_validated_snapshot", type_="foreignkey"
        )
        batch_op.drop_column("validated_cv_snapshot_id")
    with op.batch_alter_table("validated_cv_snapshots") as batch_op:
        batch_op.drop_index("ix_validated_cv_snapshots_task")
        batch_op.create_unique_constraint(
            "uq_validated_cv_snapshots_task", ["cv_extraction_task_id"]
        )
        for name in (
            "source_file_id",
            "source_file_sha256",
            "raw_text_sha256",
            "snapshot_revision",
            "supersedes_snapshot_id",
            "confirmed_by",
            "confirmed_at",
            "extraction_provider",
            "model",
            "prompt_version",
            "extraction_schema_version",
            "normalization_version",
            "taxonomy_version",
            "field_decisions",
            "evidence_payload",
        ):
            batch_op.drop_column(name)
    with op.batch_alter_table("cv_extraction_tasks") as batch_op:
        for name in (
            "review_payload",
            "review_fingerprint",
            "confirmation_status",
            "latest_validated_cv_snapshot_id",
            "confirmed_at",
            "confirmed_by",
            "review_revision",
            "confirmation_idempotency_key",
            "confirmation_idempotency_fingerprint",
        ):
            batch_op.drop_column(name)
