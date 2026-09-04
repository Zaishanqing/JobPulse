"""Relax obsolete evaluation task fingerprint columns.

Revision ID: 20260811_0009
Revises: 20260811_0008

Legacy deployments required three fingerprint columns that the current task
contract no longer writes. Keep their historical values, but allow new rows to
use the versioned profile-ID contract.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260811_0009"
down_revision: str | None = "20260811_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_COLUMNS = (
    "input_fingerprint",
    "cv_profile_fingerprint",
    "position_profile_fingerprint",
)


def _column_map() -> dict[str, dict[str, object]]:
    return {
        item["name"]: item
        for item in sa.inspect(op.get_bind()).get_columns("evaluation_tasks")
    }


def upgrade() -> None:
    columns = _column_map()
    for name in LEGACY_COLUMNS:
        column = columns.get(name)
        if column is not None and not bool(column["nullable"]):
            op.alter_column(
                "evaluation_tasks",
                name,
                existing_type=column["type"],
                nullable=True,
            )


def downgrade() -> None:
    # Historical rows can be preserved, but new rows intentionally do not
    # populate these obsolete fields. Reintroducing NOT NULL would make the
    # current writer unusable, so the safe downgrade is schema-preserving.
    pass
