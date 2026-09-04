"""PostgreSQL baseline for append-only emerging discovery history."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_postgresql_baseline"
down_revision = None
branch_labels = None
depends_on = None

HISTORY_TABLES = (
    "discovery_runs",
    "input_snapshots",
    "algorithm_config_snapshots",
    "clusters",
    "cluster_memberships",
    "cluster_lineages",
    "germination_assessments",
)


def _history_columns():
    return (
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("emerging discovery baseline supports PostgreSQL only")

    op.create_table(
        "discovery_runs",
        *_history_columns(),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("algorithm_version", sa.String(64), nullable=False),
        sa.Column("formula_version", sa.String(64), nullable=False),
        sa.Column("time_window_start", sa.Date()),
        sa.Column("time_window_end", sa.Date()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('succeeded', 'failed')", name="ck_discovery_runs_status"),
    )
    op.create_index("ix_discovery_runs_request_id", "discovery_runs", ["request_id"])
    op.create_index(
        "ix_discovery_runs_time_window",
        "discovery_runs",
        ["time_window_start", "time_window_end"],
    )

    op.create_table(
        "input_snapshots",
        *_history_columns(),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("discovery_runs.id"), nullable=False),
        sa.Column("source_jd_id", sa.String(128), nullable=False),
        sa.Column("window_id", sa.String(64), nullable=False),
        sa.Column("input_version", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("run_id", "source_jd_id", name="uq_input_snapshots_run_source_jd"),
    )
    op.create_index("ix_input_snapshots_run_id", "input_snapshots", ["run_id"])
    op.create_index(
        "ix_input_snapshots_run_window", "input_snapshots", ["run_id", "window_id"]
    )

    op.create_table(
        "algorithm_config_snapshots",
        *_history_columns(),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("discovery_runs.id"), nullable=False),
        sa.Column("algorithm_version", sa.String(64), nullable=False),
        sa.Column("formula_version", sa.String(64), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("run_id", name="uq_algorithm_config_snapshots_run_id"),
    )

    op.create_table(
        "clusters",
        *_history_columns(),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("discovery_runs.id"), nullable=False),
        sa.Column("cluster_key", sa.String(128), nullable=False),
        sa.Column("cluster_name", sa.String(255), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("core_skills", postgresql.JSONB(), nullable=False),
        sa.Column("representative_titles", postgresql.JSONB(), nullable=False),
        sa.Column("representative_members", postgresql.JSONB(), nullable=False),
        sa.Column("core_responsibilities", postgresql.JSONB(), nullable=False),
        sa.Column("semantic_centroid", postgresql.JSONB(), nullable=False),
        sa.Column("algorithm_sources", postgresql.JSONB(), nullable=False),
        sa.Column("merge_basis", postgresql.JSONB(), nullable=False),
        sa.Column("stability_score", sa.Float(), nullable=False),
        sa.Column("growth_score", sa.Float(), nullable=False),
        sa.Column("distance_from_existing_positions", sa.Float(), nullable=False),
        sa.Column("feature_summary", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("stability_score BETWEEN 0 AND 1", name="ck_clusters_stability"),
        sa.CheckConstraint("growth_score BETWEEN 0 AND 1", name="ck_clusters_growth"),
        sa.CheckConstraint(
            "distance_from_existing_positions BETWEEN 0 AND 1", name="ck_clusters_distance"
        ),
        sa.UniqueConstraint("run_id", "cluster_key", name="uq_clusters_run_key"),
    )
    op.create_index("ix_clusters_run_id", "clusters", ["run_id"])

    op.create_table(
        "cluster_memberships",
        *_history_columns(),
        sa.Column("cluster_id", sa.String(36), sa.ForeignKey("clusters.id"), nullable=False),
        sa.Column(
            "input_snapshot_id",
            sa.String(36),
            sa.ForeignKey("input_snapshots.id"),
            nullable=False,
        ),
        sa.Column("membership_score", sa.Float(), nullable=False),
        sa.CheckConstraint("membership_score BETWEEN 0 AND 1", name="ck_membership_score"),
        sa.UniqueConstraint(
            "cluster_id", "input_snapshot_id", name="uq_cluster_memberships_cluster_snapshot"
        ),
    )
    op.create_index("ix_cluster_memberships_cluster_id", "cluster_memberships", ["cluster_id"])
    op.create_index(
        "ix_cluster_memberships_input_snapshot_id",
        "cluster_memberships",
        ["input_snapshot_id"],
    )

    op.create_table(
        "cluster_lineages",
        *_history_columns(),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("discovery_runs.id"), nullable=False),
        sa.Column("predecessor_cluster_id", sa.String(36), sa.ForeignKey("clusters.id")),
        sa.Column("successor_cluster_id", sa.String(36), sa.ForeignKey("clusters.id")),
        sa.Column("relation_type", sa.String(32), nullable=False),
        sa.Column("similarity_score", sa.Float(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("decision_version", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "relation_type IN ('birth', 'continue', 'split', 'merge', 'decline', 'absorbed')",
            name="ck_cluster_lineage_relation",
        ),
        sa.CheckConstraint("similarity_score BETWEEN 0 AND 1", name="ck_lineage_score"),
    )
    op.create_index("ix_cluster_lineages_run_id", "cluster_lineages", ["run_id"])
    op.create_index(
        "ix_cluster_lineages_predecessor_cluster_id",
        "cluster_lineages",
        ["predecessor_cluster_id"],
    )
    op.create_index(
        "ix_cluster_lineages_successor_cluster_id",
        "cluster_lineages",
        ["successor_cluster_id"],
    )

    op.create_table(
        "germination_assessments",
        *_history_columns(),
        sa.Column("cluster_id", sa.String(36), sa.ForeignKey("clusters.id"), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("level", sa.String(32), nullable=False),
        sa.Column("qualified_as_emerging", sa.Boolean(), nullable=False),
        sa.Column("dimensions", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_package", postgresql.JSONB(), nullable=False),
        sa.Column("generated_definition", postgresql.JSONB(), nullable=False),
        sa.Column("decision_reason", sa.Text(), nullable=False),
        sa.CheckConstraint("score BETWEEN 0 AND 1", name="ck_assessment_score"),
        sa.UniqueConstraint("cluster_id", name="uq_germination_assessments_cluster_id"),
    )

    op.create_table(
        "discovery_maintenance_audits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_discovery_maintenance_audits_run_id", "discovery_maintenance_audits", ["run_id"]
    )

    op.execute(
        """
        CREATE FUNCTION reject_discovery_history_mutation() RETURNS trigger AS $$
        BEGIN
            IF current_setting('jobgraph.allow_discovery_cleanup', true) = 'on' THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION '% records are append-only', TG_TABLE_NAME
                USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in HISTORY_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_reject_mutation BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_discovery_history_mutation()"
        )


def downgrade() -> None:
    for table in reversed(HISTORY_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_reject_mutation ON {table}")
    op.execute("DROP FUNCTION IF EXISTS reject_discovery_history_mutation()")
    for table in (
        "discovery_maintenance_audits",
        "germination_assessments",
        "cluster_lineages",
        "cluster_memberships",
        "clusters",
        "algorithm_config_snapshots",
        "input_snapshots",
        "discovery_runs",
    ):
        op.drop_table(table)
