"""Persist explicit Candidate lineage relations and review decisions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0006_candidate_lineage"
down_revision = "0005_candidate_window_unique"
branch_labels = None
depends_on = None


def _history_columns() -> tuple[sa.Column, ...]:
    return (
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("emerging discovery supports PostgreSQL only")

    op.create_table(
        "candidate_lineage_relations",
        *_history_columns(),
        sa.Column("relation_id", sa.String(160), nullable=False),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("discovery_runs.id"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.String(32), nullable=False),
        sa.Column("source_candidate_ids", postgresql.JSONB(), nullable=False),
        sa.Column("target_candidate_ids", postgresql.JSONB(), nullable=False),
        sa.Column("source_window_id", sa.String(64), nullable=False),
        sa.Column("target_window_id", sa.String(64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("decision_basis", postgresql.JSONB(), nullable=False),
        sa.Column("review_required", sa.Boolean(), nullable=False),
        sa.Column("algorithm_version", sa.String(128), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("source_cluster_ids", postgresql.JSONB(), nullable=False),
        sa.Column("target_cluster_ids", postgresql.JSONB(), nullable=False),
        sa.Column(
            "proposed_target_candidate_ids",
            postgresql.JSONB(),
            nullable=False,
        ),
        sa.Column("support_inflation", sa.Integer(), nullable=False),
        sa.Column("observation_delta", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "relation_id",
            name="uq_candidate_lineage_relation_id",
        ),
        sa.CheckConstraint(
            "relation_type IN ('CONTINUE', 'SPLIT', 'MERGE')",
            name="ck_candidate_lineage_relation_type",
        ),
        sa.CheckConstraint(
            "confidence BETWEEN 0 AND 1",
            name="ck_candidate_lineage_confidence",
        ),
        sa.CheckConstraint(
            "support_inflation = 0",
            name="ck_candidate_lineage_support_inflation",
        ),
        sa.CheckConstraint(
            "observation_delta = 0",
            name="ck_candidate_lineage_observation_delta",
        ),
    )
    op.create_index(
        "ix_candidate_lineage_run_id",
        "candidate_lineage_relations",
        ["run_id"],
    )
    op.create_index(
        "ix_candidate_lineage_transition",
        "candidate_lineage_relations",
        ["source_window_id", "target_window_id"],
    )
    op.create_index(
        "ix_candidate_lineage_source_candidates",
        "candidate_lineage_relations",
        ["source_candidate_ids"],
        postgresql_using="gin",
        postgresql_ops={"source_candidate_ids": "jsonb_path_ops"},
    )
    op.create_index(
        "ix_candidate_lineage_target_candidates",
        "candidate_lineage_relations",
        ["target_candidate_ids"],
        postgresql_using="gin",
        postgresql_ops={"target_candidate_ids": "jsonb_path_ops"},
    )
    op.execute(
        "CREATE TRIGGER candidate_lineage_relations_reject_mutation "
        "BEFORE UPDATE OR DELETE ON candidate_lineage_relations "
        "FOR EACH ROW EXECUTE FUNCTION reject_discovery_history_mutation()"
    )

    op.create_table(
        "candidate_lineage_reviews",
        *_history_columns(),
        sa.Column("review_id", sa.String(160), nullable=False),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("discovery_runs.id"),
            nullable=False,
        ),
        sa.Column("source_window_id", sa.String(64), nullable=False),
        sa.Column("target_window_id", sa.String(64), nullable=False),
        sa.Column("cluster_ids", postgresql.JSONB(), nullable=False),
        sa.Column("candidate_ids", postgresql.JSONB(), nullable=False),
        sa.Column("decision_basis", postgresql.JSONB(), nullable=False),
        sa.Column("hypotheses", postgresql.JSONB(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("algorithm_version", sa.String(128), nullable=False),
        sa.UniqueConstraint(
            "review_id",
            name="uq_candidate_lineage_review_id",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence BETWEEN 0 AND 1)",
            name="ck_candidate_lineage_review_confidence",
        ),
    )
    op.create_index(
        "ix_candidate_lineage_review_run_id",
        "candidate_lineage_reviews",
        ["run_id"],
    )
    op.create_index(
        "ix_candidate_lineage_review_transition",
        "candidate_lineage_reviews",
        ["source_window_id", "target_window_id"],
    )
    op.execute(
        "CREATE TRIGGER candidate_lineage_reviews_reject_mutation "
        "BEFORE UPDATE OR DELETE ON candidate_lineage_reviews "
        "FOR EACH ROW EXECUTE FUNCTION reject_discovery_history_mutation()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS candidate_lineage_reviews_reject_mutation "
        "ON candidate_lineage_reviews"
    )
    op.drop_index(
        "ix_candidate_lineage_review_transition",
        table_name="candidate_lineage_reviews",
    )
    op.drop_index(
        "ix_candidate_lineage_review_run_id",
        table_name="candidate_lineage_reviews",
    )
    op.drop_table("candidate_lineage_reviews")
    op.execute(
        "DROP TRIGGER IF EXISTS candidate_lineage_relations_reject_mutation "
        "ON candidate_lineage_relations"
    )
    op.drop_index(
        "ix_candidate_lineage_target_candidates",
        table_name="candidate_lineage_relations",
    )
    op.drop_index(
        "ix_candidate_lineage_source_candidates",
        table_name="candidate_lineage_relations",
    )
    op.drop_index(
        "ix_candidate_lineage_transition",
        table_name="candidate_lineage_relations",
    )
    op.drop_index(
        "ix_candidate_lineage_run_id",
        table_name="candidate_lineage_relations",
    )
    op.drop_table("candidate_lineage_relations")
