"""Replace the legacy 18-family position catalog with taxonomy v2.

Revision ID: 20260801_45
Revises: 20260801_44
"""

from alembic import op
import sqlalchemy as sa


revision = "20260801_45"
down_revision = "20260801_44"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    columns = {item["name"] for item in inspector.get_columns("standard_positions")}
    unique_names = {
        item["name"]
        for item in inspector.get_unique_constraints("standard_positions")
        if item.get("name")
    }
    with op.batch_alter_table("standard_positions") as batch:
        if "uq_standard_positions_taxonomy_family_code" in unique_names:
            batch.drop_constraint(
                "uq_standard_positions_taxonomy_family_code", type_="unique"
            )
        if "position_code" not in columns:
            batch.add_column(sa.Column("position_code", sa.String(100), nullable=True))
        if "skill_domain_codes" not in columns:
            batch.add_column(
                sa.Column(
                    "skill_domain_codes",
                    sa.JSON(),
                    nullable=False,
                    server_default="[]",
                )
            )
        batch.create_unique_constraint(
            "uq_standard_positions_position_code", ["position_code"]
        )
        batch.create_index(
            "ix_standard_positions_position_code", ["position_code"], unique=False
        )


def downgrade() -> None:
    raise RuntimeError("position taxonomy v2 is an intentional no-compatibility migration")
