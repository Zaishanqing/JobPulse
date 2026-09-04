"""persist relation statistics and explanation payloads

Revision ID: 0015_relation_insights
Revises: 0014_skill_taxonomy_projection
"""

import sqlalchemy as sa
from alembic import op


revision = "0015_relation_insights"
down_revision = "0014_skill_taxonomy_projection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("position_skill_relation_drafts") as batch:
        batch.add_column(
            sa.Column(
                "statistics",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch.add_column(
            sa.Column(
                "explanation",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("position_skill_relation_drafts") as batch:
        batch.drop_column("explanation")
        batch.drop_column("statistics")
