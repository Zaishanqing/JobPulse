"""Merge requirement graph and position taxonomy migration branches.

Revision ID: 20260809_65
Revises: 20260808_64, 20260809_63
"""

from collections.abc import Sequence


revision: str = "20260809_65"
down_revision: tuple[str, str] = ("20260808_64", "20260809_63")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Both parent migrations own their schema changes; this revision only
    # restores one linear upgrade target after the branches are combined.
    pass


def downgrade() -> None:
    # Downgrading the merge restores both parent heads without changing schema.
    pass
