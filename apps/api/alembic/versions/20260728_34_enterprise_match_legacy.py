"""mark enterprise_job match reports as legacy and add matching infrastructure

Revision ID: 20260728_34
Revises: 20260727_33
"""

import sqlalchemy as sa
from alembic import op


revision = "20260728_34"
down_revision = "20260727_33"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # 1. Mark historical enterprise_job MatchReport as legacy
    if "match_reports" in inspector.get_table_names():
        match_columns = {c["name"] for c in inspector.get_columns("match_reports")}
        if "target_type" in match_columns and "provider" in match_columns:
            op.execute(
                "UPDATE match_reports SET provider = 'enterprise_match_v1_legacy', "
                "status = 'stale' "
                "WHERE target_type = 'enterprise_job' "
                "AND provider != 'enterprise_match_v1_legacy'"
            )
            op.execute(
                "UPDATE match_reports SET algorithm_version = 'enterprise-skill-weight-v1' "
                "WHERE target_type = 'enterprise_job' "
                "AND algorithm_version = 'legacy'"
            )

    # 2. Add evaluation_id, task_id, algorithm_version to candidate_decisions
    if "candidate_decisions" in inspector.get_table_names():
        decision_columns = {c["name"] for c in inspector.get_columns("candidate_decisions")}
        if "evaluation_id" not in decision_columns:
            with op.batch_alter_table("candidate_decisions") as batch_op:
                batch_op.add_column(
                    sa.Column("evaluation_id", sa.String(200), nullable=True)
                )
                batch_op.add_column(
                    sa.Column("task_id", sa.String(200), nullable=True)
                )
                batch_op.add_column(
                    sa.Column("algorithm_version", sa.String(128), nullable=True)
                )
            op.create_index(
                "ix_candidate_decisions_evaluation_id",
                "candidate_decisions",
                ["evaluation_id"],
            )

    # 3. Add target_type to matching_service_references
    if "matching_service_references" in inspector.get_table_names():
        ref_columns = {c["name"] for c in inspector.get_columns("matching_service_references")}
        if "target_type" not in ref_columns:
            with op.batch_alter_table("matching_service_references") as batch_op:
                batch_op.add_column(
                    sa.Column(
                        "target_type",
                        sa.String(64),
                        nullable=False,
                        server_default="standard_position",
                    )
                )

    # 4. Create matching_submission_intents table
    if "matching_submission_intents" not in inspector.get_table_names():
        op.create_table(
            "matching_submission_intents",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
            sa.Column("user_id", sa.String(36), nullable=False),
            sa.Column("tenant_ref", sa.String(64), nullable=False),
            sa.Column("resume_id", sa.String(36), nullable=False),
            sa.Column("position_id", sa.String(200), nullable=False),
            sa.Column("target_type", sa.String(64), nullable=False, server_default="enterprise_job"),
            sa.Column("cv_profile_fingerprint", sa.String(64), nullable=False),
            sa.Column("position_profile_fingerprint", sa.String(64), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="intended"),
            sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
            sa.Column("last_error_code", sa.String(128), nullable=True),
            sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("idempotency_key_hash", name="uq_intent_idempotency_key_hash"),
            sa.CheckConstraint(
                "status IN ("
                "'intended','rejected','remote_unknown','reference_pending',"
                "'reference_saved','abandoned'"
                ")",
                name="ck_matching_submission_intents_status",
            ),
        )
        op.create_index(
            "ix_intent_status_next_retry",
            "matching_submission_intents",
            ["status", "next_retry_at"],
        )
        op.create_index(
            "ix_matching_submission_intents_user_id",
            "matching_submission_intents",
            ["user_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Check for irreversible business data
    if "candidate_decisions" in inspector.get_table_names():
        columns = {c["name"] for c in inspector.get_columns("candidate_decisions")}
        if "evaluation_id" in columns:
            result = bind.execute(
                sa.text("SELECT COUNT(*) FROM candidate_decisions WHERE evaluation_id IS NOT NULL")
            ).scalar()
            if result and result > 0:
                raise RuntimeError(
                    "irreversible: candidate_decisions with evaluation_id exist"
                )

    if "matching_service_references" in inspector.get_table_names():
        columns = {c["name"] for c in inspector.get_columns("matching_service_references")}
        if "target_type" in columns:
            result = bind.execute(
                sa.text(
                    "SELECT COUNT(*) FROM matching_service_references "
                    "WHERE target_type = 'enterprise_job'"
                )
            ).scalar()
            if result and result > 0:
                raise RuntimeError(
                    "irreversible: matching_service_references with enterprise_job exist"
                )

    if "matching_submission_intents" in inspector.get_table_names():
        result = bind.execute(
            sa.text("SELECT COUNT(*) FROM matching_submission_intents")
        ).scalar()
        if result and result > 0:
            raise RuntimeError(
                "irreversible: matching_submission_intents contain records"
            )

    # Safe to drop new structures
    if "candidate_decisions" in inspector.get_table_names():
        columns = {c["name"] for c in inspector.get_columns("candidate_decisions")}
        if "evaluation_id" in columns:
            op.drop_index("ix_candidate_decisions_evaluation_id", table_name="candidate_decisions")
            with op.batch_alter_table("candidate_decisions") as batch_op:
                batch_op.drop_column("algorithm_version")
                batch_op.drop_column("task_id")
                batch_op.drop_column("evaluation_id")

    if "matching_service_references" in inspector.get_table_names():
        columns = {c["name"] for c in inspector.get_columns("matching_service_references")}
        if "target_type" in columns:
            with op.batch_alter_table("matching_service_references") as batch_op:
                batch_op.drop_column("target_type")

    if "matching_submission_intents" in inspector.get_table_names():
        op.drop_index("ix_matching_submission_intents_user_id", table_name="matching_submission_intents")
        op.drop_index("ix_intent_status_next_retry", table_name="matching_submission_intents")
        op.drop_table("matching_submission_intents")

    # Historical match_reports marking is NOT reversed (data integrity)
