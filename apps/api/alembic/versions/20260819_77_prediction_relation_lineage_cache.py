"""Add prediction relation lineage and match/definition cache keys.

Revision ID: 20260819_77
Revises: 20260819_76

Pre-lineage relation rows do not carry enough information to reconstruct their
historical update/delete chain.  The migration therefore assigns each legacy row
its own relation identity; this is a deterministic preservation of legacy rows,
not an attempt to guess the original lineage.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260819_77"
down_revision = ("20260819_76", "20260817_75")
branch_labels = None
depends_on = None


def _column_names(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    relation_columns = _column_names("predicted_position_relation_versions")
    if "relation_identity_id" not in relation_columns:
        op.add_column(
            "predicted_position_relation_versions",
            sa.Column(
                "relation_identity_id",
                sa.String(36),
                nullable=False,
                server_default="",
            ),
        )
        op.execute(
            "UPDATE predicted_position_relation_versions "
            "SET relation_identity_id = id WHERE relation_identity_id = ''"
        )
        # Legacy rows become their own root identities. Historical relations
        # that existed before lineage tracking cannot be recovered reliably.
        op.create_index(
            "ix_predicted_position_relation_versions_relation_identity_id",
            "predicted_position_relation_versions",
            ["relation_identity_id"],
        )
    if "supersedes_relation_id" not in relation_columns:
        op.add_column(
            "predicted_position_relation_versions",
            sa.Column("supersedes_relation_id", sa.String(36), nullable=True),
        )

    match_columns = _column_names("predicted_position_matches")
    if "cache_key" not in match_columns:
        op.add_column(
            "predicted_position_matches",
            sa.Column(
                "cache_key",
                sa.String(128),
                nullable=False,
                server_default="legacy",
            ),
        )

    definition_columns = _column_names("predicted_position_definition_versions")
    if "cache_key" not in definition_columns:
        op.add_column(
            "predicted_position_definition_versions",
            sa.Column(
                "cache_key",
                sa.String(128),
                nullable=False,
                server_default="legacy",
            ),
        )

    acquisition_indexes = {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes("acquisition_jobs")
    }
    if "ix_acquisition_jobs_requested_by" not in acquisition_indexes:
        op.create_index(
            "ix_acquisition_jobs_requested_by",
            "acquisition_jobs",
            ["requested_by"],
            unique=False,
        )


def downgrade() -> None:
    relation_columns = _column_names("predicted_position_relation_versions")
    if "supersedes_relation_id" in relation_columns:
        op.drop_column("predicted_position_relation_versions", "supersedes_relation_id")
    if "relation_identity_id" in relation_columns:
        op.drop_index(
            "ix_predicted_position_relation_versions_relation_identity_id",
            table_name="predicted_position_relation_versions",
        )
        op.drop_column("predicted_position_relation_versions", "relation_identity_id")

    match_columns = _column_names("predicted_position_matches")
    if "cache_key" in match_columns:
        op.drop_column("predicted_position_matches", "cache_key")

    definition_columns = _column_names("predicted_position_definition_versions")
    if "cache_key" in definition_columns:
        op.drop_column("predicted_position_definition_versions", "cache_key")

    acquisition_indexes = {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes("acquisition_jobs")
    }
    if "ix_acquisition_jobs_requested_by" in acquisition_indexes:
        op.drop_index(
            "ix_acquisition_jobs_requested_by",
            table_name="acquisition_jobs",
        )
