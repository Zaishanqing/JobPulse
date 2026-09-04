"""add knowledge graph integration mappings

Revision ID: 20260713_14
Revises: 20260713_13
Create Date: 2026-07-13
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260713_14"
down_revision: Union[str, Sequence[str], None] = "20260713_13"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("knowledge_graph_entity_mappings"):
        return
    op.create_table(
        "knowledge_graph_entity_mappings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("main_system_id", sa.String(length=80), nullable=False),
        sa.Column("knowledge_graph_id", sa.String(length=80), nullable=True),
        sa.Column("payload_hash", sa.String(length=64), nullable=True),
        sa.Column("sync_status", sa.String(length=64), nullable=False),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("last_trace_id", sa.String(length=64), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "entity_type in ('document', 'position', 'skill', 'graph_version')",
            name="ck_kg_mapping_entity_type",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "entity_type", "main_system_id", name="uq_kg_mapping_entity_main_id"
        ),
    )
    op.create_index(
        op.f("ix_knowledge_graph_entity_mappings_entity_type"),
        "knowledge_graph_entity_mappings", ["entity_type"], unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_graph_entity_mappings_main_system_id"),
        "knowledge_graph_entity_mappings", ["main_system_id"], unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_graph_entity_mappings_knowledge_graph_id"),
        "knowledge_graph_entity_mappings", ["knowledge_graph_id"], unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_knowledge_graph_entity_mappings_knowledge_graph_id"),
        table_name="knowledge_graph_entity_mappings",
    )
    op.drop_index(
        op.f("ix_knowledge_graph_entity_mappings_main_system_id"),
        table_name="knowledge_graph_entity_mappings",
    )
    op.drop_index(
        op.f("ix_knowledge_graph_entity_mappings_entity_type"),
        table_name="knowledge_graph_entity_mappings",
    )
    op.drop_table("knowledge_graph_entity_mappings")
