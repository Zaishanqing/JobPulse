"""track the authoritative validated CV snapshot used by each resume

Revision ID: 20260726_30
Revises: 20260725_29
"""

from alembic import op
import sqlalchemy as sa


revision = "20260726_30"
down_revision = "20260725_29"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {
        column["name"] for column in sa.inspect(bind).get_columns("resumes")
    }
    if "validated_cv_snapshot_id" in columns:
        return
    # Nullable preserves direct text/file/image resumes and historical CV
    # resumes whose authoritative snapshot was not recorded.
    with op.batch_alter_table("resumes") as batch_op:
        batch_op.add_column(
            sa.Column("validated_cv_snapshot_id", sa.String(36), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_resumes_validated_cv_snapshot_id",
            "validated_cv_snapshots",
            ["validated_cv_snapshot_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index(
            "ix_resumes_validated_cv_snapshot_id",
            ["validated_cv_snapshot_id"],
            unique=False,
        )


def downgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("resumes")
    }
    if "validated_cv_snapshot_id" not in columns:
        return
    with op.batch_alter_table("resumes") as batch_op:
        batch_op.drop_index("ix_resumes_validated_cv_snapshot_id")
        batch_op.drop_constraint(
            "fk_resumes_validated_cv_snapshot_id", type_="foreignkey"
        )
        batch_op.drop_column("validated_cv_snapshot_id")
