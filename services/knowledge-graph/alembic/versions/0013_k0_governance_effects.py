"""persist effective mapping and reviewed dependency authority

Revision ID: 0013_k0_governance_effects
Revises: 0012_k0_release_lineage
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_k0_governance_effects"
down_revision = "0012_k0_release_lineage"
branch_labels = None
depends_on = None

IMMUTABLE_TABLES = (
    "effective_mapping_records",
    "dependency_review_decisions",
    "graph_version_dependencies",
)


def _identity_columns() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade():
    op.create_table(
        "effective_mapping_records",
        *_identity_columns(),
        sa.Column("review_decision_id", sa.Integer(), nullable=False),
        sa.Column("supersedes_effective_mapping_id", sa.Integer()),
        sa.Column("source_fact_id", sa.String(100), nullable=False),
        sa.Column("requirement_id", sa.String(80), nullable=False),
        sa.Column("source_expression", sa.String(300), nullable=False),
        sa.Column("skill_id", sa.String(80), nullable=False),
        sa.Column("policy_version", sa.String(100), nullable=False),
        sa.ForeignKeyConstraint(["review_decision_id"], ["mapping_review_decisions.id"]),
        sa.ForeignKeyConstraint(["supersedes_effective_mapping_id"], ["effective_mapping_records.id"]),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.skill_id"]),
        sa.UniqueConstraint("review_decision_id", "source_fact_id", "requirement_id"),
    )
    op.create_index("ix_effective_mapping_records_review_decision_id", "effective_mapping_records", ["review_decision_id"])
    op.create_index("ix_effective_mapping_records_source_fact_id", "effective_mapping_records", ["source_fact_id"])
    op.create_index("ix_effective_mapping_records_requirement_id", "effective_mapping_records", ["requirement_id"])
    op.create_table(
        "dependency_review_decisions",
        *_identity_columns(),
        sa.Column("dependency_candidate_id", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(10), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.String(100), nullable=False),
        sa.Column("decided_at", sa.String(80), nullable=False),
        sa.ForeignKeyConstraint(["dependency_candidate_id"], ["dependency_candidates.id"]),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"]),
        sa.UniqueConstraint("dependency_candidate_id"),
        sa.CheckConstraint("decision IN ('accept', 'reject')"),
    )
    op.create_index("ix_dependency_review_decisions_dependency_candidate_id", "dependency_review_decisions", ["dependency_candidate_id"])
    op.create_table(
        "graph_version_dependencies",
        *_identity_columns(),
        sa.Column("graph_version_id", sa.Integer(), nullable=False),
        sa.Column("dependency_candidate_id", sa.Integer(), nullable=False),
        sa.Column("review_decision_id", sa.Integer(), nullable=False),
        sa.Column("prerequisite_skill_id", sa.String(80), nullable=False),
        sa.Column("advanced_skill_id", sa.String(80), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("claim_kind", sa.String(30), nullable=False),
        sa.Column("policy_version", sa.String(100), nullable=False),
        sa.ForeignKeyConstraint(["graph_version_id"], ["graph_versions.id"]),
        sa.ForeignKeyConstraint(["dependency_candidate_id"], ["dependency_candidates.id"]),
        sa.ForeignKeyConstraint(["review_decision_id"], ["dependency_review_decisions.id"]),
        sa.ForeignKeyConstraint(["prerequisite_skill_id"], ["skills.skill_id"]),
        sa.ForeignKeyConstraint(["advanced_skill_id"], ["skills.skill_id"]),
        sa.UniqueConstraint("graph_version_id", "prerequisite_skill_id", "advanced_skill_id"),
        sa.CheckConstraint("claim_kind = 'reviewed'"),
    )
    op.create_index("ix_graph_version_dependencies_graph_version_id", "graph_version_dependencies", ["graph_version_id"])
    _create_guards()


def _create_guards():
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for table in IMMUTABLE_TABLES:
            op.execute(f"CREATE TRIGGER trg_{table}_reject_update BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT, '{table} is immutable'); END")
            op.execute(f"CREATE TRIGGER trg_{table}_reject_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT, '{table} is immutable'); END")
    elif dialect == "postgresql":
        op.execute("""CREATE FUNCTION reject_k0_governance_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'K0 governance artifact is immutable' USING ERRCODE = 'integrity_constraint_violation'; END; $$""")
        for table in IMMUTABLE_TABLES:
            op.execute(f"CREATE TRIGGER trg_{table}_reject_update BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_k0_governance_mutation()")
            op.execute(f"CREATE TRIGGER trg_{table}_reject_delete BEFORE DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_k0_governance_mutation()")
    else:
        raise RuntimeError(f"K0 governance migration does not support dialect {dialect!r}")


def downgrade():
    raise RuntimeError("Migration 0013 is forward-only and cannot be downgraded")
