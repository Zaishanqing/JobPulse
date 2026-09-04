"""Drop legacy MatchReport and learning-path tables.

The matching main chain now only stores Matching Service references and
Evaluation Contract projections. Historical reports are not migrated.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260803_49"
down_revision = "20260802_48"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "learning_paths" in op.get_bind().dialect.get_table_names(op.get_bind()):
        op.drop_table("learning_paths")
    if "match_reports" in op.get_bind().dialect.get_table_names(op.get_bind()):
        op.drop_table("match_reports")


def downgrade() -> None:
    op.create_table(
        "match_reports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("resume_id", sa.String(length=36), nullable=False),
        sa.Column("validated_cv_snapshot_id", sa.String(length=36), nullable=True),
        sa.Column("resume_profile_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("position_profile_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("algorithm_version", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("rule_based", sa.Boolean(), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.String(length=36), nullable=False),
        sa.Column("use_enterprise_weights", sa.Boolean(), nullable=False),
        sa.Column("overall_score", sa.Float(), nullable=False),
        sa.Column("radar", sa.JSON(), nullable=False),
        sa.Column("matched_skills", sa.JSON(), nullable=False),
        sa.Column("missing_skills", sa.JSON(), nullable=False),
        sa.Column("weak_skills", sa.JSON(), nullable=False),
        sa.Column("bonus_skills", sa.JSON(), nullable=False),
        sa.Column("project_match", sa.JSON(), nullable=False),
        sa.Column("explanation", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('current', 'stale')", name="ck_match_reports_status"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["validated_cv_snapshot_id"],
            ["validated_cv_snapshots.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("match_reports", schema=None) as batch_op:
        batch_op.create_index("ix_match_reports_resume_id", ["resume_id"], unique=False)
        batch_op.create_index("ix_match_reports_target_id", ["target_id"], unique=False)
        batch_op.create_index("ix_match_reports_user_id", ["user_id"], unique=False)
        batch_op.create_index(
            "ix_match_reports_validated_cv_snapshot_id",
            ["validated_cv_snapshot_id"],
            unique=False,
        )

    op.create_table(
        "learning_paths",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("match_report_id", sa.String(length=36), nullable=False),
        sa.Column("target_position_id", sa.String(length=36), nullable=True),
        sa.Column("duration_weeks", sa.Integer(), nullable=False),
        sa.Column("learning_goal", sa.Text(), nullable=False),
        sa.Column("stages", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["match_report_id"], ["match_reports.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("learning_paths", schema=None) as batch_op:
        batch_op.create_index(
            "ix_learning_paths_match_report_id", ["match_report_id"], unique=False
        )
        batch_op.create_index(
            "ix_learning_paths_target_position_id", ["target_position_id"], unique=False
        )
        batch_op.create_index("ix_learning_paths_user_id", ["user_id"], unique=False)
