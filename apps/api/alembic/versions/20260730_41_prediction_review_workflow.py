"""Add versioned predicted-position matching and review workflow.

Revision ID: 20260730_41
Revises: 20260730_40
"""

from alembic import op
import sqlalchemy as sa


revision = "20260730_41"
down_revision = "20260730_40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_tables = set(inspector.get_table_names())
    predicted_columns = {
        column["name"] for column in inspector.get_columns("predicted_positions")
    }
    workflow_tables = {
        "predicted_position_matches",
        "predicted_position_definition_versions",
        "predicted_position_relation_versions",
    }
    # Historical application startup used create_all. In that hybrid state the
    # new schema can already exist before Alembic reaches this revision.
    if "published_definition_version_id" not in predicted_columns:
        op.add_column(
            "predicted_positions",
            sa.Column("published_definition_version_id", sa.String(36), nullable=True),
        )
        op.create_index(
            "ix_predicted_positions_published_definition_version_id",
            "predicted_positions",
            ["published_definition_version_id"],
        )
    if "published_at" not in predicted_columns:
        op.add_column(
            "predicted_positions",
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        )
    if workflow_tables <= existing_tables:
        return
    op.create_table(
        "predicted_position_matches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("predicted_position_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=False),
        sa.Column("similarity_score", sa.Float(), nullable=False),
        sa.Column("matched_skills", sa.JSON(), nullable=False),
        sa.Column("missing_skills", sa.JSON(), nullable=False),
        sa.Column("overlap_evidence", sa.JSON(), nullable=False),
        sa.Column("recommendation", sa.String(32), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["predicted_position_id"], ["predicted_positions.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.UniqueConstraint(
            "predicted_position_id", "version", "target_type", "target_id",
            name="uq_prediction_match_version_target",
        ),
        sa.CheckConstraint(
            "recommendation in ('new_candidate', 'possible_duplicate', "
            "'possible_evolution', 'insufficient_evidence')",
            name="ck_prediction_match_recommendation",
        ),
    )
    op.create_index("ix_predicted_position_matches_predicted_position_id", "predicted_position_matches", ["predicted_position_id"])
    op.create_index("ix_predicted_position_matches_input_fingerprint", "predicted_position_matches", ["input_fingerprint"])
    op.create_table(
        "predicted_position_definition_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("predicted_position_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("definition_payload", sa.JSON(), nullable=False),
        sa.Column("review_task_id", sa.String(36), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["predicted_position_id"], ["predicted_positions.id"]),
        sa.ForeignKeyConstraint(["review_task_id"], ["review_tasks.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.UniqueConstraint("predicted_position_id", "version", name="uq_prediction_definition_version"),
        sa.UniqueConstraint("predicted_position_id", "input_fingerprint", name="uq_prediction_definition_fingerprint"),
        sa.CheckConstraint(
            "status in ('draft', 'in_review', 'approved', 'rejected', 'published')",
            name="ck_prediction_definition_status",
        ),
    )
    op.create_index("ix_predicted_position_definition_versions_predicted_position_id", "predicted_position_definition_versions", ["predicted_position_id"])
    op.create_table(
        "predicted_position_relation_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("predicted_position_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("relation_type", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["predicted_position_id"], ["predicted_positions.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.UniqueConstraint("predicted_position_id", "version", name="uq_prediction_relation_version"),
        sa.CheckConstraint(
            "relation_type in ('standard_position', 'emerging_position', 'independent')",
            name="ck_prediction_relation_type",
        ),
        sa.CheckConstraint("status in ('active', 'deleted')", name="ck_prediction_relation_status"),
    )
    op.create_index("ix_predicted_position_relation_versions_predicted_position_id", "predicted_position_relation_versions", ["predicted_position_id"])


def downgrade() -> None:
    op.drop_index("ix_predicted_position_relation_versions_predicted_position_id", table_name="predicted_position_relation_versions")
    op.drop_table("predicted_position_relation_versions")
    op.drop_index("ix_predicted_position_definition_versions_predicted_position_id", table_name="predicted_position_definition_versions")
    op.drop_table("predicted_position_definition_versions")
    op.drop_index("ix_predicted_position_matches_input_fingerprint", table_name="predicted_position_matches")
    op.drop_index("ix_predicted_position_matches_predicted_position_id", table_name="predicted_position_matches")
    op.drop_table("predicted_position_matches")
    op.drop_index("ix_predicted_positions_published_definition_version_id", table_name="predicted_positions")
    op.drop_column("predicted_positions", "published_at")
    op.drop_column("predicted_positions", "published_definition_version_id")
