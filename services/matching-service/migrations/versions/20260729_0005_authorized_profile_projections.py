"""persist authorized profile projection lineage

Revision ID: 20260729_0005
Revises: 20260729_0004
"""

import sqlalchemy as sa
from alembic import op

revision = "20260729_0005"
down_revision = "20260729_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vector_index_references", sa.Column("source_entity_id", sa.String(200)))
    op.add_column("vector_index_references", sa.Column("target_type", sa.String(80)))
    op.add_column("vector_index_references", sa.Column("grant_id", sa.String(200)))
    op.add_column("vector_index_references", sa.Column("grant_version", sa.Integer()))
    op.add_column(
        "vector_index_references", sa.Column("personal_tenant_ref", sa.String(200))
    )
    op.add_column(
        "vector_index_references", sa.Column("enterprise_tenant_ref", sa.String(200))
    )
    with op.batch_alter_table("vector_index_references") as batch:
        batch.drop_constraint("uq_vector_index_reference_lineage", type_="unique")
        batch.create_unique_constraint(
            "uq_vector_index_reference_lineage",
            (
                "tenant_ref",
                "entity_type",
                "entity_id",
                "fragment_id",
                "profile_version",
                "embedding_revision",
                "grant_id",
                "grant_version",
            ),
        )


def downgrade() -> None:
    with op.batch_alter_table("vector_index_references") as batch:
        batch.drop_constraint("uq_vector_index_reference_lineage", type_="unique")
        batch.create_unique_constraint(
            "uq_vector_index_reference_lineage",
            (
                "tenant_ref",
                "entity_type",
                "entity_id",
                "fragment_id",
                "profile_version",
                "embedding_revision",
            ),
        )
    for name in (
        "enterprise_tenant_ref",
        "personal_tenant_ref",
        "grant_version",
        "grant_id",
        "target_type",
        "source_entity_id",
    ):
        op.drop_column("vector_index_references", name)
