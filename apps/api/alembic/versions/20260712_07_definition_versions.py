"""persist emerging position definition versions

Revision ID: 20260712_07
Revises: 20260712_06
Create Date: 2026-07-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260712_07"
down_revision: Union[str, Sequence[str], None] = "20260712_06"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "emerging_definition_versions" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "emerging_definition_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("emerging_id", sa.String(length=36), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["emerging_id"], ["emerging_positions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_emerging_definition_versions_emerging_id"),
        "emerging_definition_versions",
        ["emerging_id"],
    )
    op.create_index(
        op.f("ix_emerging_definition_versions_selected"),
        "emerging_definition_versions",
        ["selected"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_emerging_definition_versions_selected"),
        table_name="emerging_definition_versions",
    )
    op.drop_index(
        op.f("ix_emerging_definition_versions_emerging_id"),
        table_name="emerging_definition_versions",
    )
    op.drop_table("emerging_definition_versions")
