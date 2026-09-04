"""merge CV semantic demo and trend review migration branches

Revision ID: 20260804_56
Revises: 20260804_53, 20260804_54
"""

from alembic import op


revision = "20260804_56"
down_revision = ("20260804_53", "20260804_54")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
