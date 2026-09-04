"""enforce idempotent emerging projections

Revision ID: 20260716_17
Revises: 20260715_16
"""

from alembic import op


revision = "20260716_17"
down_revision = "20260715_16"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("emerging_positions") as batch:
        batch.create_unique_constraint(
            "uq_emerging_positions_cluster_id", ["cluster_id"]
        )
    with op.batch_alter_table("standard_positions") as batch:
        batch.create_unique_constraint(
            "uq_standard_positions_source_emerging_position_id",
            ["source_emerging_position_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("standard_positions") as batch:
        batch.drop_constraint(
            "uq_standard_positions_source_emerging_position_id", type_="unique"
        )
    with op.batch_alter_table("emerging_positions") as batch:
        batch.drop_constraint("uq_emerging_positions_cluster_id", type_="unique")
