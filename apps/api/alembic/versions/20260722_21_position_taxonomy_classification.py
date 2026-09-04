"""store taxonomy classification on standard positions

Revision ID: 20260722_21
Revises: 20260720_20
"""

from alembic import op
import sqlalchemy as sa


revision = "20260722_21"
down_revision = "20260720_20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("standard_positions") as batch:
        batch.add_column(sa.Column("taxonomy_family_code", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("taxonomy_family_name", sa.String(length=120), nullable=True))
        batch.create_unique_constraint(
            "uq_standard_positions_taxonomy_family_code", ["taxonomy_family_code"]
        )
        batch.create_index(
            "ix_standard_positions_taxonomy_family_code", ["taxonomy_family_code"]
        )


def downgrade() -> None:
    with op.batch_alter_table("standard_positions") as batch:
        batch.drop_index("ix_standard_positions_taxonomy_family_code")
        batch.drop_constraint("uq_standard_positions_taxonomy_family_code", type_="unique")
        batch.drop_column("taxonomy_family_name")
        batch.drop_column("taxonomy_family_code")
