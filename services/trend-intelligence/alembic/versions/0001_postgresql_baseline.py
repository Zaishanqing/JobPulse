"""Fresh PostgreSQL baseline for Trend Intelligence and Acquisition.

Revision ID: 0001_postgresql_baseline
Revises:
"""

from alembic import op

from app.infrastructure.database import Base
import app.infrastructure.models  # noqa: F401
import app.acquisition.infrastructure.acquisition_models  # noqa: F401


revision = "0001_postgresql_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
