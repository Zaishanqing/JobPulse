"""Candidate identity and lifecycle persistence."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0002_candidate_lifecycle"
down_revision = "0001_postgresql_baseline"
branch_labels = None
depends_on = None

STATUSES = (
    "weak_signal",
    "incubating",
    "emerging_candidate",
    "stable_emerging_role",
    "official_position",
    "dead",
    "noise",
)


def _status_list() -> str:
    return ", ".join(f"'{value}'" for value in STATUSES)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("emerging discovery supports PostgreSQL only")

    op.create_table(
        "candidates",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("first_seen_window_id", sa.String(64), nullable=False),
        sa.Column("last_seen_window_id", sa.String(64), nullable=False),
        sa.Column("age", sa.Integer(), nullable=False),
        sa.Column("current_cluster_id", sa.String(36), sa.ForeignKey("clusters.id")),
        sa.Column("previous_cluster_ids", postgresql.JSONB(), nullable=False),
        sa.Column("canonical_title", sa.String(255), nullable=False),
        sa.Column("display_title", sa.String(255), nullable=False),
        sa.Column("definition", postgresql.JSONB(), nullable=False),
        sa.Column("support_count", sa.Integer(), nullable=False),
        sa.Column("company_coverage", sa.Integer(), nullable=False),
        sa.Column("skill_similarity", sa.Float()),
        sa.Column("responsibility_similarity", sa.Float()),
        sa.Column("title_similarity", sa.Float()),
        sa.Column("membership_overlap", sa.Float()),
        sa.Column("identity_similarity", sa.Float(), nullable=False),
        sa.Column("novelty_score", sa.Float(), nullable=False),
        sa.Column("emergence_score", sa.Float(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("identity_stability", sa.Integer(), nullable=False),
        sa.Column("identity_profile", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"status IN ({_status_list()})",
            name="ck_candidates_status",
        ),
        sa.CheckConstraint("age >= 0", name="ck_candidates_age"),
        sa.CheckConstraint("support_count >= 0", name="ck_candidates_support"),
        sa.CheckConstraint("company_coverage >= 0", name="ck_candidates_companies"),
        sa.CheckConstraint(
            "identity_similarity BETWEEN 0 AND 1",
            name="ck_candidates_identity",
        ),
        sa.CheckConstraint("novelty_score BETWEEN 0 AND 1", name="ck_candidates_novelty"),
        sa.CheckConstraint("emergence_score BETWEEN 0 AND 1", name="ck_candidates_emergence"),
    )
    op.create_index("ix_candidates_status", "candidates", ["status"])
    op.create_index(
        "ix_candidates_last_seen_window", "candidates", ["last_seen_window_id"]
    )
    op.create_index("ix_candidates_current_cluster_id", "candidates", ["current_cluster_id"])

    op.create_table(
        "candidate_cluster_observations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "candidate_id",
            sa.String(64),
            sa.ForeignKey("candidates.id"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("discovery_runs.id"),
            nullable=False,
        ),
        sa.Column("cluster_id", sa.String(36), sa.ForeignKey("clusters.id"), nullable=False),
        sa.Column("window_id", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("emergence_score", sa.Float(), nullable=False),
        sa.Column("support_count", sa.Integer(), nullable=False),
        sa.Column("company_count", sa.Integer(), nullable=False),
        sa.Column("identity_similarity", sa.Float(), nullable=False),
        sa.Column("skill_similarity", sa.Float()),
        sa.Column("responsibility_similarity", sa.Float()),
        sa.Column("title_similarity", sa.Float()),
        sa.Column("membership_overlap", sa.Float()),
        sa.Column("semantic_similarity", sa.Float()),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("match_evidence", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            f"status IN ({_status_list()})",
            name="ck_candidate_observation_status",
        ),
        sa.CheckConstraint(
            "emergence_score BETWEEN 0 AND 1",
            name="ck_candidate_obs_emergence",
        ),
        sa.CheckConstraint(
            "identity_similarity BETWEEN 0 AND 1",
            name="ck_candidate_obs_identity",
        ),
        sa.CheckConstraint("support_count >= 0", name="ck_candidate_obs_support"),
        sa.CheckConstraint("company_count >= 0", name="ck_candidate_obs_companies"),
        sa.UniqueConstraint(
            "candidate_id",
            "cluster_id",
            name="uq_candidate_observation_cluster",
        ),
    )
    op.create_index(
        "ix_candidate_observations_candidate_id",
        "candidate_cluster_observations",
        ["candidate_id"],
    )
    op.create_index(
        "ix_candidate_observations_run_id",
        "candidate_cluster_observations",
        ["run_id"],
    )
    op.create_index(
        "ix_candidate_observations_cluster_id",
        "candidate_cluster_observations",
        ["cluster_id"],
    )
    op.create_index(
        "ix_candidate_observations_window",
        "candidate_cluster_observations",
        ["candidate_id", "window_id"],
    )

    op.create_table(
        "candidate_status_transitions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "candidate_id",
            sa.String(64),
            sa.ForeignKey("candidates.id"),
            nullable=False,
        ),
        sa.Column("from_status", sa.String(32)),
        sa.Column("to_status", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("discovery_runs.id")),
        sa.Column("window_id", sa.String(64), nullable=False),
        sa.Column("transition_version", sa.String(64), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"from_status IS NULL OR from_status IN ({_status_list()})",
            name="ck_candidate_transition_from",
        ),
        sa.CheckConstraint(
            f"to_status IN ({_status_list()})",
            name="ck_candidate_transition_to",
        ),
    )
    op.create_index(
        "ix_candidate_transitions_candidate_id",
        "candidate_status_transitions",
        ["candidate_id"],
    )
    op.create_index(
        "ix_candidate_transitions_window",
        "candidate_status_transitions",
        ["candidate_id", "window_id"],
    )
    op.create_index(
        "ix_candidate_transitions_run_id",
        "candidate_status_transitions",
        ["run_id"],
    )

    for table in ("candidate_cluster_observations", "candidate_status_transitions"):
        op.execute(
            f"CREATE TRIGGER {table}_reject_mutation BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_discovery_history_mutation()"
        )


def downgrade() -> None:
    for table in ("candidate_cluster_observations", "candidate_status_transitions"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_reject_mutation ON {table}")
    op.drop_table("candidate_status_transitions")
    op.drop_table("candidate_cluster_observations")
    op.drop_table("candidates")
