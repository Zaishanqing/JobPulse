"""persist TraceSkill claims, watermarks, candidates and projections

Revision ID: 0011_traceskill_innovation_planes
Revises: 0010_published_fact_lineage
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0011_traceskill_innovation_planes"
down_revision = "0010_published_fact_lineage"
branch_labels = None
depends_on = None

IMMUTABLE_TABLES = (
    "build_input_watermarks",
    "relation_claims",
    "mapping_review_decisions",
    "dependency_analysis_runs",
    "dependency_candidates",
    "projection_manifests",
)


def _identity_columns() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade():
    op.create_table(
        "build_input_watermarks",
        *_identity_columns(),
        sa.Column("build_run_id", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("source_facts", sa.JSON(), nullable=False),
        sa.Column("observation_window_start", sa.String(80), nullable=False),
        sa.Column("observation_window_end", sa.String(80), nullable=False),
        sa.Column("catalog_snapshot_id", sa.String(120), nullable=False),
        sa.Column("catalog_content_hash", sa.String(64), nullable=False),
        sa.Column("validation_state", sa.String(10), nullable=False),
        sa.Column("validation_policy_version", sa.String(100)),
        sa.Column("mapping_policy_version", sa.String(100), nullable=False),
        sa.Column("aggregation_algorithm_version", sa.String(100), nullable=False),
        sa.Column("normalized_config", sa.JSON(), nullable=False),
        sa.Column("normalized_config_hash", sa.String(64), nullable=False),
        sa.Column("input_coverage", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["build_run_id"], ["graph_build_runs.id"]),
        sa.UniqueConstraint("build_run_id"),
        sa.CheckConstraint("validation_state IN ('present', 'absent')"),
        sa.CheckConstraint(
            "(validation_state = 'present' AND validation_policy_version IS NOT NULL) "
            "OR (validation_state = 'absent' AND validation_policy_version IS NULL)"
        ),
        sa.CheckConstraint("input_coverage >= 0 AND input_coverage <= 1"),
    )
    op.create_index("ix_build_input_watermarks_build_run_id", "build_input_watermarks", ["build_run_id"])

    op.create_table(
        "relation_claims",
        *_identity_columns(),
        sa.Column("claim_id", sa.String(64), nullable=False),
        sa.Column("graph_version_id", sa.Integer(), nullable=False),
        sa.Column("build_run_id", sa.Integer(), nullable=False),
        sa.Column("support_id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.String(80), nullable=False),
        sa.Column("predicate", sa.String(50), nullable=False),
        sa.Column("object_id", sa.String(80), nullable=False),
        sa.Column("claim_kind", sa.String(30), nullable=False),
        sa.Column("source_kind", sa.String(30), nullable=False),
        sa.Column("source_fact_id", sa.String(100), nullable=False),
        sa.Column("source_fact_version", sa.String(100), nullable=False),
        sa.Column("requirement_id", sa.String(80), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("validation_lineage_fingerprint", sa.String(64)),
        sa.Column("catalog_snapshot_fingerprint", sa.String(64), nullable=False),
        sa.Column("mapping_policy_version", sa.String(100), nullable=False),
        sa.Column("observed_at", sa.String(80), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["graph_version_id"], ["graph_versions.id"]),
        sa.ForeignKeyConstraint(["build_run_id"], ["graph_build_runs.id"]),
        sa.ForeignKeyConstraint(["support_id"], ["position_skill_supports.id"]),
        sa.ForeignKeyConstraint(["subject_id"], ["standard_positions.position_id"]),
        sa.ForeignKeyConstraint(["object_id"], ["skills.skill_id"]),
        sa.UniqueConstraint("claim_id"),
        sa.UniqueConstraint("graph_version_id", "support_id"),
        sa.CheckConstraint("claim_kind IN ('observed', 'reviewed')"),
        sa.CheckConstraint("source_kind IN ('published_fact', 'legacy_local')"),
    )
    op.create_index("ix_relation_claims_claim_id", "relation_claims", ["claim_id"])
    op.create_index("ix_relation_claims_graph_version_id", "relation_claims", ["graph_version_id"])
    op.create_index("ix_relation_claims_build_run_id", "relation_claims", ["build_run_id"])

    op.create_table(
        "mapping_candidates",
        *_identity_columns(),
        sa.Column("candidate_id", sa.String(80), nullable=False),
        sa.Column("source_expression", sa.String(300), nullable=False),
        sa.Column("proposed_skill_id", sa.String(80), nullable=False),
        sa.Column("signals", sa.JSON(), nullable=False),
        sa.Column("priority", sa.Float(), nullable=False),
        sa.Column("model_version", sa.String(100), nullable=False),
        sa.Column("index_version", sa.String(100), nullable=False),
        sa.Column("mapping_policy_version", sa.String(100), nullable=False),
        sa.Column("affected_contexts", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(["proposed_skill_id"], ["skills.skill_id"]),
        sa.UniqueConstraint("candidate_id"),
        sa.CheckConstraint("status IN ('pending', 'accepted', 'rejected', 'no_match', 'superseded')"),
        sa.CheckConstraint("revision >= 1"),
        sa.CheckConstraint("priority >= 0 AND priority <= 1"),
    )
    op.create_index("ix_mapping_candidates_candidate_id", "mapping_candidates", ["candidate_id"])
    op.create_index("ix_mapping_candidates_source_expression", "mapping_candidates", ["source_expression"])
    op.create_index("ix_mapping_candidates_priority", "mapping_candidates", ["priority"])

    op.create_table(
        "mapping_review_decisions",
        *_identity_columns(),
        sa.Column("candidate_id", sa.String(80), nullable=False),
        sa.Column("candidate_revision", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.String(100), nullable=False),
        sa.Column("decided_at", sa.String(80), nullable=False),
        sa.Column("effective_scope", sa.String(120), nullable=False),
        sa.Column("replacement_candidate_id", sa.String(80)),
        sa.ForeignKeyConstraint(["candidate_id"], ["mapping_candidates.candidate_id"]),
        sa.ForeignKeyConstraint(
            ["replacement_candidate_id"], ["mapping_candidates.candidate_id"]
        ),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"]),
        sa.UniqueConstraint("candidate_id", "candidate_revision"),
        sa.CheckConstraint("decision IN ('accept', 'reject', 'no_match', 'supersede')"),
        sa.CheckConstraint(
            "(decision = 'supersede' AND replacement_candidate_id IS NOT NULL) "
            "OR (decision != 'supersede' AND replacement_candidate_id IS NULL)"
        ),
    )
    op.create_index(
        "ix_mapping_review_decisions_candidate_id",
        "mapping_review_decisions",
        ["candidate_id"],
    )

    op.create_table(
        "dependency_analysis_runs",
        *_identity_columns(),
        sa.Column("build_run_id", sa.Integer(), nullable=False),
        sa.Column("policy_hash", sa.String(64), nullable=False),
        sa.Column("policy", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["build_run_id"], ["graph_build_runs.id"]),
        sa.UniqueConstraint("build_run_id", "policy_hash"),
    )
    op.create_index("ix_dependency_analysis_runs_build_run_id", "dependency_analysis_runs", ["build_run_id"])

    op.create_table(
        "dependency_candidates",
        *_identity_columns(),
        sa.Column("analysis_run_id", sa.Integer(), nullable=False),
        sa.Column("prerequisite_skill_id", sa.String(80), nullable=False),
        sa.Column("advanced_skill_id", sa.String(80), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("claim_kind", sa.String(30), nullable=False),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["dependency_analysis_runs.id"]),
        sa.ForeignKeyConstraint(["prerequisite_skill_id"], ["skills.skill_id"]),
        sa.ForeignKeyConstraint(["advanced_skill_id"], ["skills.skill_id"]),
        sa.UniqueConstraint("analysis_run_id", "prerequisite_skill_id", "advanced_skill_id"),
        sa.CheckConstraint("claim_kind = 'inferred_candidate'"),
    )
    op.create_index("ix_dependency_candidates_analysis_run_id", "dependency_candidates", ["analysis_run_id"])

    op.create_table(
        "projection_manifests",
        *_identity_columns(),
        sa.Column("graph_version_id", sa.Integer(), nullable=False),
        sa.Column("projection_version", sa.String(100), nullable=False),
        sa.Column("watermark_fingerprint", sa.String(64), nullable=False),
        sa.Column("node_count", sa.Integer(), nullable=False),
        sa.Column("edge_count", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["graph_version_id"], ["graph_versions.id"]),
        sa.UniqueConstraint("graph_version_id", "projection_version"),
        sa.CheckConstraint("node_count >= 0"),
        sa.CheckConstraint("edge_count >= 0"),
    )
    op.create_index("ix_projection_manifests_graph_version_id", "projection_manifests", ["graph_version_id"])
    _create_immutability_guards()


def _create_immutability_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for table in IMMUTABLE_TABLES:
            op.execute(
                f"CREATE TRIGGER trg_{table}_reject_update BEFORE UPDATE ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{table} is immutable'); END"
            )
            op.execute(
                f"CREATE TRIGGER trg_{table}_reject_delete BEFORE DELETE ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{table} is immutable'); END"
            )
    elif dialect == "postgresql":
        op.execute("""
            CREATE FUNCTION reject_traceskill_immutable_mutation() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'TraceSkill immutable record cannot be changed'
                    USING ERRCODE = 'integrity_constraint_violation';
            END;
            $$
        """)
        for table in IMMUTABLE_TABLES:
            op.execute(
                f"CREATE TRIGGER trg_{table}_reject_update BEFORE UPDATE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION reject_traceskill_immutable_mutation()"
            )
            op.execute(
                f"CREATE TRIGGER trg_{table}_reject_delete BEFORE DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION reject_traceskill_immutable_mutation()"
            )
    else:
        raise RuntimeError(f"TraceSkill migration does not support dialect {dialect!r}")


def downgrade():
    raise RuntimeError("Migration 0011 is forward-only and cannot be downgraded")
