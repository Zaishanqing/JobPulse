"""add matching-service authority references

Revision ID: 20260727_33
Revises: 20260726_32
"""

import sqlalchemy as sa
from alembic import op


revision = "20260727_33"
down_revision = "20260726_32"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "matching_service_references" in inspector.get_table_names():
        legacy_required = {
            "id", "task_id", "evaluation_id", "user_id", "tenant_ref",
            "resume_id", "position_id", "provider", "status",
            "idempotency_key_hash", "created_at", "updated_at",
        }
        actual = {column["name"] for column in inspector.get_columns("matching_service_references")}
        if "idempotency_key_hash" in actual:
            expected = legacy_required
        else:
            # The explicit idempotency/version model replaces hashed keys and
            # fingerprints on newer deployments.
            expected = legacy_required - {
                "tenant_ref",
                "idempotency_key_hash",
            } | {
                "tenant_id",
                "idempotency_key",
                "cv_profile_version",
                "position_profile_version",
            }
        # A historical create_all hybrid may already contain columns introduced
        # by later revisions. Accept only the explicitly known forward schema.
        known_future = {
            "target_type",
            "schema_version",
            "access_scope",
            "source_version",
            "cv_profile_fingerprint",
            "position_profile_fingerprint",
            "cv_profile_version",
            "position_profile_version",
            "tenant_id",
            "idempotency_key",
            "taxonomy_version",
            "graph_version",
            "algorithm_version",
            "matching_method",
            "degraded",
            "overall_score",
            "error_code",
            "error_message",
        }
        if not expected <= actual or actual - expected - known_future:
            raise RuntimeError("Existing matching_service_references schema is incompatible")
        return
    op.create_table(
        "matching_service_references",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(200), nullable=False),
        sa.Column("evaluation_id", sa.String(200), nullable=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("tenant_ref", sa.String(64), nullable=False),
        sa.Column("resume_id", sa.String(36), nullable=False),
        sa.Column("position_id", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "idempotency_key_hash",
            name="uq_matching_service_reference_idempotency",
        ),
    )
    op.create_index(
        "ix_matching_service_reference_user_created",
        "matching_service_references",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_matching_service_references_task_id",
        "matching_service_references",
        ["task_id"],
        unique=True,
    )
    op.create_index(
        "ix_matching_service_references_evaluation_id",
        "matching_service_references",
        ["evaluation_id"],
        unique=True,
    )
    op.create_index(
        "ix_matching_service_references_user_id",
        "matching_service_references",
        ["user_id"],
    )


def downgrade() -> None:
    if "matching_service_references" not in sa.inspect(op.get_bind()).get_table_names():
        return
    op.drop_index("ix_matching_service_references_user_id", table_name="matching_service_references")
    op.drop_index("ix_matching_service_references_evaluation_id", table_name="matching_service_references")
    op.drop_index("ix_matching_service_references_task_id", table_name="matching_service_references")
    op.drop_index("ix_matching_service_reference_user_created", table_name="matching_service_references")
    op.drop_table("matching_service_references")
