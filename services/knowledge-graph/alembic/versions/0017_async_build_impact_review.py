"""durable build jobs and dependency impact records

Revision ID: 0017_async_build_impact_review
Revises: 0016_graph_version_dependencies
"""

from alembic import op
import sqlalchemy as sa


revision = "0017_async_build_impact_review"
down_revision = "0016_graph_version_dependencies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "graph_build_jobs",
        sa.Column("job_key", sa.String(64), nullable=False),
        sa.Column("position_id", sa.String(80), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("command", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("build_run_id", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("worker_id", sa.String(100), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND max_attempts >= 1 AND attempts <= max_attempts",
        ),
        sa.ForeignKeyConstraint(["position_id"], ["standard_positions.position_id"]),
        sa.ForeignKeyConstraint(["build_run_id"], ["graph_build_runs.id"]),
        sa.UniqueConstraint("job_key"),
        sa.UniqueConstraint("build_run_id"),
    )
    op.create_index("ix_graph_build_jobs_job_key", "graph_build_jobs", ["job_key"])
    op.create_index("ix_graph_build_jobs_position_id", "graph_build_jobs", ["position_id"])
    op.create_index("ix_graph_build_jobs_status", "graph_build_jobs", ["status"])

    op.create_table(
        "downstream_dependency_references",
        sa.Column("consumer_system", sa.String(30), nullable=False),
        sa.Column("reference_type", sa.String(80), nullable=False),
        sa.Column("reference_id", sa.String(120), nullable=False),
        sa.Column("graph_version_id", sa.Integer(), nullable=False),
        sa.Column("metadata_payload", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "consumer_system IN ('matching', 'trend', 'discovery')",
        ),
        sa.ForeignKeyConstraint(["graph_version_id"], ["graph_versions.id"]),
        sa.UniqueConstraint(
            "consumer_system", "reference_type", "reference_id", "graph_version_id",
            name="uq_downstream_dependency_reference",
        ),
    )
    op.create_index(
        "ix_downstream_dependency_references_consumer_system",
        "downstream_dependency_references",
        ["consumer_system"],
    )
    op.create_index(
        "ix_downstream_dependency_references_reference_id",
        "downstream_dependency_references",
        ["reference_id"],
    )
    op.create_index(
        "ix_downstream_dependency_references_graph_version_id",
        "downstream_dependency_references",
        ["graph_version_id"],
    )

    op.create_table(
        "dependency_events",
        sa.Column("event_key", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.String(100), nullable=False),
        sa.Column("change_kind", sa.String(50), nullable=False),
        sa.Column("before_snapshot", sa.JSON(), nullable=False),
        sa.Column("after_snapshot", sa.JSON(), nullable=False),
        sa.Column("impact_snapshot", sa.JSON(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("trace_id", sa.String(80), nullable=False),
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.UniqueConstraint("event_key"),
    )
    op.create_index("ix_dependency_events_event_key", "dependency_events", ["event_key"])
    op.create_index("ix_dependency_events_entity_type", "dependency_events", ["entity_type"])
    op.create_index("ix_dependency_events_entity_id", "dependency_events", ["entity_id"])


def downgrade() -> None:
    op.drop_table("dependency_events")
    op.drop_table("downstream_dependency_references")
    op.drop_table("graph_build_jobs")
