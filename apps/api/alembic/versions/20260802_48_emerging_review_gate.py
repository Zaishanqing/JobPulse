"""Add evidence-backed review and immutable publication snapshots.

Revision ID: 20260802_48
Revises: 20260802_47
"""

from alembic import op
import sqlalchemy as sa


revision = "20260802_48"
down_revision = "20260802_47"
branch_labels = None
depends_on = None


_NEW_STATUS_CHECK = (
    "status in ('draft', 'pending_review', 'approved', 'published', 'rejected')"
)
_OLD_STATUS_CHECK = (
    "status in ('pending_review', 'verified', 'published', 'rejected')"
)


def upgrade() -> None:
    # SQLite cannot ALTER constraints directly. Alembic batch mode recreates the
    # table there while continuing to emit ordinary ALTER TABLE operations on
    # PostgreSQL, so the same migration history remains testable on both.
    with op.batch_alter_table("emerging_positions") as batch_op:
        batch_op.drop_constraint(
            "ck_emerging_positions_status_allowed",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_emerging_positions_status_allowed",
            _NEW_STATUS_CHECK,
        )
        batch_op.add_column(
            sa.Column(
                "field_evidence",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "review_history",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch_op.add_column(
            sa.Column("approved_definition_hash", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("published_snapshot", sa.JSON(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("emerging_positions") as batch_op:
        batch_op.drop_column("published_snapshot")
        batch_op.drop_column("approved_definition_hash")
        batch_op.drop_column("review_history")
        batch_op.drop_column("field_evidence")
        batch_op.drop_constraint(
            "ck_emerging_positions_status_allowed",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_emerging_positions_status_allowed",
            _OLD_STATUS_CHECK,
        )
