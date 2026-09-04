"""Persist identity resolution audits for ambiguous identity write-back."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0004_identity_resolution_audit"
down_revision = "0003_candidate_lifecycle_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("emerging discovery supports PostgreSQL only")

    op.create_table(
        "identity_resolution_audits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "provisional_candidate_id",
            sa.String(64),
            sa.ForeignKey("candidates.id"),
            nullable=False,
        ),
        sa.Column("target_candidate_id", sa.String(64), nullable=True),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("reviewer", sa.String(128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("window_id", sa.String(64), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("algorithm_version", sa.String(128), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            "decision IN ('confirm_same', 'confirm_new')",
            name="ck_identity_resolution_decision",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_identity_resolution_idempotency_key",
        ),
    )
    op.create_index(
        "ix_identity_resolution_provisional_candidate",
        "identity_resolution_audits",
        ["provisional_candidate_id"],
    )
    op.execute(
        "CREATE TRIGGER identity_resolution_audits_reject_mutation "
        "BEFORE UPDATE OR DELETE ON identity_resolution_audits "
        "FOR EACH ROW EXECUTE FUNCTION reject_discovery_history_mutation()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS identity_resolution_audits_reject_mutation "
        "ON identity_resolution_audits"
    )
    op.drop_index(
        "ix_identity_resolution_provisional_candidate",
        table_name="identity_resolution_audits",
    )
    op.drop_table("identity_resolution_audits")
