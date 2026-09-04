"""add matching lineage and CV execution metadata

Revision ID: 20260726_31
Revises: 20260726_30
"""

import sqlalchemy as sa
from alembic import op


revision = "20260726_31"
down_revision = "20260726_30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    match_columns = {
        column["name"] for column in inspector.get_columns("match_reports")
    }
    matching_lineage_columns = {
        "validated_cv_snapshot_id",
        "resume_profile_fingerprint",
        "position_profile_fingerprint",
        "algorithm_version",
        "provider",
        "rule_based",
    }
    existing_lineage_columns = matching_lineage_columns & match_columns
    if existing_lineage_columns and existing_lineage_columns != matching_lineage_columns:
        missing = ", ".join(
            sorted(matching_lineage_columns - existing_lineage_columns)
        )
        raise RuntimeError(
            "Existing match_reports lineage schema is incomplete; "
            f"missing columns: {missing}"
        )
    if not existing_lineage_columns:
        # SQLite batch mode rebuilds the table so the historical resume_id
        # column can gain RESTRICT semantics without dialect-specific SQL.
        with op.batch_alter_table("match_reports") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "validated_cv_snapshot_id", sa.String(36), nullable=True
                )
            )
            batch_op.add_column(
                sa.Column(
                    "resume_profile_fingerprint",
                    sa.String(71),
                    nullable=False,
                    server_default="",
                )
            )
            batch_op.add_column(
                sa.Column(
                    "position_profile_fingerprint",
                    sa.String(71),
                    nullable=False,
                    server_default="",
                )
            )
            batch_op.add_column(
                sa.Column(
                    "algorithm_version",
                    sa.String(64),
                    nullable=False,
                    server_default="legacy",
                )
            )
            batch_op.add_column(
                sa.Column(
                    "provider",
                    sa.String(64),
                    nullable=False,
                    server_default="unknown",
                )
            )
            batch_op.add_column(
                sa.Column(
                    "rule_based",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.true(),
                )
            )
            batch_op.create_foreign_key(
                "fk_match_reports_resume_id",
                "resumes",
                ["resume_id"],
                ["id"],
                ondelete="RESTRICT",
            )
            batch_op.create_foreign_key(
                "fk_match_reports_validated_cv_snapshot_id",
                "validated_cv_snapshots",
                ["validated_cv_snapshot_id"],
                ["id"],
                ondelete="RESTRICT",
            )
            batch_op.create_index(
                "ix_match_reports_validated_cv_snapshot_id",
                ["validated_cv_snapshot_id"],
            )
    unexpected_status = bind.execute(
        sa.text(
            "SELECT status FROM match_reports "
            "WHERE status NOT IN ('completed', 'current', 'stale') LIMIT 1"
        )
    ).scalar_one_or_none()
    if unexpected_status is not None:
        raise RuntimeError(
            "Unexpected match report status must be corrected before migration: "
            f"{unexpected_status}"
        )
    op.execute(
        "UPDATE match_reports SET status = 'current' "
        "WHERE status = 'completed'"
    )
    constraints = {
        item["name"]
        for item in sa.inspect(bind).get_check_constraints("match_reports")
    }
    if "ck_match_reports_status" not in constraints:
        with op.batch_alter_table("match_reports") as batch_op:
            batch_op.create_check_constraint(
                "ck_match_reports_status",
                "status IN ('current', 'stale')",
            )

    task_columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("cv_extraction_tasks")
    }
    with op.batch_alter_table("cv_extraction_tasks") as batch_op:
        if "execution_fingerprint" not in task_columns:
            batch_op.add_column(
                sa.Column("execution_fingerprint", sa.String(71), nullable=True)
            )
        if "execution_metadata" not in task_columns:
            batch_op.add_column(
                sa.Column("execution_metadata", sa.JSON(), nullable=True)
            )

    snapshot_columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("validated_cv_snapshots")
    }
    if "execution_metadata" not in snapshot_columns:
        with op.batch_alter_table("validated_cv_snapshots") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "execution_metadata",
                    sa.JSON(),
                    nullable=False,
                    server_default="{}",
                )
            )


def downgrade() -> None:
    with op.batch_alter_table("validated_cv_snapshots") as batch_op:
        batch_op.drop_column("execution_metadata")
    with op.batch_alter_table("cv_extraction_tasks") as batch_op:
        batch_op.drop_column("execution_metadata")
        batch_op.drop_column("execution_fingerprint")
    with op.batch_alter_table("match_reports") as batch_op:
        batch_op.drop_constraint("ck_match_reports_status", type_="check")
        batch_op.drop_index("ix_match_reports_validated_cv_snapshot_id")
        batch_op.drop_constraint(
            "fk_match_reports_validated_cv_snapshot_id", type_="foreignkey"
        )
        batch_op.drop_constraint(
            "fk_match_reports_resume_id", type_="foreignkey"
        )
        batch_op.drop_column("rule_based")
        batch_op.drop_column("provider")
        batch_op.drop_column("algorithm_version")
        batch_op.drop_column("position_profile_fingerprint")
        batch_op.drop_column("resume_profile_fingerprint")
        batch_op.drop_column("validated_cv_snapshot_id")
