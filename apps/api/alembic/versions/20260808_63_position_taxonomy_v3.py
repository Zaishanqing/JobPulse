"""Add position taxonomy v3 governance fields.

Revision ID: 20260808_63
Revises: 1ce986a44e26
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260808_63"
down_revision: str | None = "1ce986a44e26"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "standard_positions",
        sa.Column("definition", sa.String(length=1000), nullable=False, server_default=""),
    )
    for name in ("aliases", "include_when", "exclude_when", "confusable_with"):
        op.add_column(
            "standard_positions",
            sa.Column(name, sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        )
    op.add_column(
        "standard_positions",
        sa.Column(
            "taxonomy_version",
            sa.String(length=64),
            nullable=False,
            server_default="position-taxonomy.v3.0.0",
        ),
    )
    op.add_column(
        "standard_positions",
        sa.Column(
            "lifecycle_status",
            sa.String(length=32),
            nullable=False,
            server_default="active",
        ),
    )
    op.add_column(
        "standard_positions",
        sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "standard_positions",
        sa.Column("replaced_by", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "standard_positions",
        sa.Column(
            "sample_support_status",
            sa.String(length=16),
            nullable=False,
            server_default="none",
        ),
    )
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("standard_positions") as batch_op:
            batch_op.create_check_constraint(
                "ck_standard_positions_lifecycle_status",
                "lifecycle_status IN ('active', 'deprecated')",
            )
            batch_op.create_check_constraint(
                "ck_standard_positions_sample_support_status",
                "sample_support_status IN ('none', 'sparse', 'sufficient')",
            )
    else:
        op.create_check_constraint(
            "ck_standard_positions_lifecycle_status",
            "standard_positions",
            "lifecycle_status IN ('active', 'deprecated')",
        )
        op.create_check_constraint(
            "ck_standard_positions_sample_support_status",
            "standard_positions",
            "sample_support_status IN ('none', 'sparse', 'sufficient')",
        )


def downgrade() -> None:
    columns = (
        "sample_support_status",
        "replaced_by",
        "deprecated_at",
        "lifecycle_status",
        "taxonomy_version",
        "confusable_with",
        "exclude_when",
        "include_when",
        "aliases",
        "definition",
    )
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("standard_positions") as batch_op:
            batch_op.drop_constraint(
                "ck_standard_positions_sample_support_status",
                type_="check",
            )
            batch_op.drop_constraint(
                "ck_standard_positions_lifecycle_status",
                type_="check",
            )
            for name in columns:
                batch_op.drop_column(name)
    else:
        op.drop_constraint(
            "ck_standard_positions_sample_support_status",
            "standard_positions",
            type_="check",
        )
        op.drop_constraint(
            "ck_standard_positions_lifecycle_status",
            "standard_positions",
            type_="check",
        )
        for name in columns:
            op.drop_column("standard_positions", name)
