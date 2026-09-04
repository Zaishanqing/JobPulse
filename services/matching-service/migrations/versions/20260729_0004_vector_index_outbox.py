"""add vector index references and vector outbox

Revision ID: 20260729_0004
Revises: 20260727_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260729_0004"
down_revision: str = "20260727_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

contract_json = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "vector_index_references",
        sa.Column("reference_id", sa.String(length=1024), primary_key=True),
        sa.Column("tenant_ref", sa.String(length=200), nullable=False),
        sa.Column("entity_type", sa.String(length=20), nullable=False),
        sa.Column("entity_id", sa.String(length=200), nullable=False),
        sa.Column("fragment_id", sa.String(length=200), nullable=False),
        sa.Column("fragment_type", sa.String(length=80), nullable=False),
        sa.Column("point_id", sa.String(length=512), nullable=False),
        sa.Column("profile_version", sa.String(length=200), nullable=False),
        sa.Column("source_version", sa.String(length=200), nullable=False),
        sa.Column("embedding_model", sa.String(length=200), nullable=False),
        sa.Column("embedding_revision", sa.String(length=200), nullable=False),
        sa.Column("vector_schema_version", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_code", sa.String(length=200)),
        sa.Column("indexed_at", sa.DateTime(timezone=True)),
        sa.Column("superseded_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_ref",
            "entity_type",
            "entity_id",
            "fragment_id",
            "profile_version",
            "embedding_revision",
            name="uq_vector_index_reference_lineage",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'embedding', 'upserting', 'indexed', "
            "'retrying', 'failed', 'superseded', 'deleted')",
            name="ck_vector_index_references_status",
        ),
    )
    op.create_index(
        "ix_vector_index_references_entity",
        "vector_index_references",
        ["tenant_ref", "entity_type", "entity_id", "status"],
    )
    op.create_table(
        "vector_outbox_events",
        sa.Column("event_id", sa.String(length=64), primary_key=True),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("deduplication_key", sa.String(length=64), nullable=False),
        sa.Column("payload", contract_json, nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_by", sa.String(length=200)),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True)),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_reference_ids", contract_json, nullable=False),
        sa.Column("last_error_code", sa.String(length=200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "deduplication_key", name="uq_vector_outbox_deduplication"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'claimed', 'retrying', 'processed', 'dead_letter')",
            name="ck_vector_outbox_events_status",
        ),
        sa.CheckConstraint(
            "attempt >= 0 AND attempt <= max_attempts",
            name="ck_vector_outbox_events_attempt",
        ),
    )
    op.create_index(
        "ix_vector_outbox_events_claim",
        "vector_outbox_events",
        ["status", "available_at", "claim_expires_at"],
    )
    op.create_table(
        "vector_outbox_audits",
        sa.Column("audit_id", sa.String(length=64), primary_key=True),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(length=20)),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.String(length=200)),
        sa.Column("correlation_id", sa.String(length=200), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["vector_outbox_events.event_id"],
            name="fk_vector_outbox_audits_event",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "event_id", "sequence", name="uq_vector_outbox_audits_sequence"
        ),
    )
    op.create_index(
        "ix_vector_outbox_audits_event_time",
        "vector_outbox_audits",
        ["event_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_vector_outbox_audits_event_time", table_name="vector_outbox_audits"
    )
    op.drop_table("vector_outbox_audits")
    op.drop_index("ix_vector_outbox_events_claim", table_name="vector_outbox_events")
    op.drop_table("vector_outbox_events")
    op.drop_index(
        "ix_vector_index_references_entity", table_name="vector_index_references"
    )
    op.drop_table("vector_index_references")
