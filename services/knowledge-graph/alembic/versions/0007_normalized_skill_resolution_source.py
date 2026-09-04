"""persist normalized skill resolution source

Revision ID: 0007_resolution_source
Revises: 0006_structured_extraction_authority
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0007_resolution_source"
down_revision = "0006_structured_extraction_authority"
branch_labels = None
depends_on = None


def upgrade():
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns(
            "normalized_skill_records"
        )
    }
    if "resolution_source" not in columns:
        with op.batch_alter_table("normalized_skill_records") as batch:
            batch.add_column(sa.Column(
                "resolution_source", sa.String(length=30),
                nullable=False, server_default="unresolved",
            ))


def downgrade():
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns(
            "normalized_skill_records"
        )
    }
    if "resolution_source" in columns:
        with op.batch_alter_table("normalized_skill_records") as batch:
            batch.drop_column("resolution_source")
