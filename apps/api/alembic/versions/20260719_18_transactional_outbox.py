"""Add the durable transactional outbox.

Revision ID: 20260719_18
Revises: 20260716_17
"""

from alembic import op
import sqlalchemy as sa


revision = "20260719_18"
down_revision = "20260716_17"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Legacy startup called ``Base.metadata.create_all`` before Alembic.  Such a
    # database can already contain the complete current table while its revision
    # is still old; preserve it and let Alembic advance the revision marker.
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("outbox_messages"):
        required_columns = {
            "id",
            "event_id",
            "event_type",
            "aggregate_id",
            "idempotency_key",
            "payload",
            "status",
            "attempts",
            "next_attempt_at",
            "lease_owner",
            "lease_until",
            "last_error",
            "trace_id",
            "occurred_at",
            "created_at",
            "updated_at",
        }
        column_details = {
            column["name"]: column for column in inspector.get_columns("outbox_messages")
        }
        existing_columns = set(column_details)
        unique_columns = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("outbox_messages")
        }
        unique_columns.update(
            tuple(index["column_names"])
            for index in inspector.get_indexes("outbox_messages")
            if index.get("unique")
        )
        missing_columns = sorted(required_columns - existing_columns)
        missing_unique = [
            column for column in ("event_id", "idempotency_key") if (column,) not in unique_columns
        ]
        indexes = {
            tuple(index["column_names"]) for index in inspector.get_indexes("outbox_messages")
        }
        missing_indexes = [
            column for column in ("aggregate_id", "status") if (column,) not in indexes
        ]
        required_not_null = required_columns - {
            "lease_owner",
            "lease_until",
            "last_error",
            "trace_id",
        }
        nullable_columns = sorted(
            column
            for column in required_not_null
            if column in column_details and column_details[column].get("nullable", True)
        )
        expected_types = {
            "id": (sa.String, 36),
            "event_id": (sa.String, 36),
            "event_type": (sa.String, 120),
            "aggregate_id": (sa.String, 120),
            "idempotency_key": (sa.String, 180),
            "payload": (sa.JSON, None),
            "status": (sa.String, 24),
            "attempts": (sa.Integer, None),
            "next_attempt_at": (sa.DateTime, None),
            "occurred_at": (sa.DateTime, None),
            "created_at": (sa.DateTime, None),
            "updated_at": (sa.DateTime, None),
        }
        invalid_types = []
        for column, (expected_type, expected_length) in expected_types.items():
            if column not in column_details:
                continue
            actual = column_details[column]["type"]
            if not isinstance(actual, expected_type) or (
                expected_length is not None and actual.length != expected_length
            ):
                invalid_types.append(column)
        if (
            missing_columns
            or missing_unique
            or missing_indexes
            or nullable_columns
            or invalid_types
        ):
            details = []
            if missing_columns:
                details.append("missing columns: " + ", ".join(missing_columns))
            if missing_unique:
                details.append("missing unique constraints: " + ", ".join(missing_unique))
            if missing_indexes:
                details.append("missing indexes: " + ", ".join(missing_indexes))
            if nullable_columns:
                details.append("unexpected nullable columns: " + ", ".join(nullable_columns))
            if invalid_types:
                details.append("unexpected column types: " + ", ".join(invalid_types))
            raise RuntimeError(
                "Existing outbox_messages table is incomplete; " + "; ".join(details)
            )
        return
    op.create_table(
        "outbox_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), nullable=False),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("aggregate_id", sa.String(120), nullable=False),
        sa.Column("idempotency_key", sa.String(180), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(80), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("event_id", name="uq_outbox_event_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_outbox_idempotency_key"),
    )
    op.create_index("ix_outbox_messages_aggregate_id", "outbox_messages", ["aggregate_id"])
    op.create_index("ix_outbox_messages_status", "outbox_messages", ["status"])


def downgrade() -> None:
    op.drop_index("ix_outbox_messages_status", table_name="outbox_messages")
    op.drop_index("ix_outbox_messages_aggregate_id", table_name="outbox_messages")
    op.drop_table("outbox_messages")
