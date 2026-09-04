"""review decisions must affect published content

Revision ID: 0026_review_decision_effects
Revises: 0025_position_taxonomy_v3
"""

from alembic import op
import sqlalchemy as sa


revision = "0026_review_decision_effects"
down_revision = "0025_position_taxonomy_v3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table(
        "position_requirement_aggregate_drafts"
    ) as batch:
        batch.add_column(
            sa.Column(
                "status",
                sa.String(length=30),
                nullable=False,
                server_default="included",
            )
        )
    with op.batch_alter_table("position_task_aggregate_drafts") as batch:
        batch.add_column(
            sa.Column(
                "status",
                sa.String(length=30),
                nullable=False,
                server_default="included",
            )
        )
    with op.batch_alter_table("review_tasks") as batch:
        batch.create_index(
            "ix_review_tasks_build_run_id_status",
            ["build_run_id", "status"],
        )
        batch.create_index(
            "ix_review_tasks_object_type_build_run_id",
            ["object_type", "build_run_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("review_tasks") as batch:
        batch.drop_index("ix_review_tasks_object_type_build_run_id")
        batch.drop_index("ix_review_tasks_build_run_id_status")
    with op.batch_alter_table("position_task_aggregate_drafts") as batch:
        batch.drop_column("status")
    with op.batch_alter_table(
        "position_requirement_aggregate_drafts"
    ) as batch:
        batch.drop_column("status")
