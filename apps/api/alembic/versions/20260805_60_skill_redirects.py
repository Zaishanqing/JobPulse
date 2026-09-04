"""retain merged skills as redirects

Revision ID: 20260805_60
Revises: 20260805_59
"""

import sqlalchemy as sa
from alembic import op


revision = "20260805_60"
down_revision = "20260805_59"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("skills")
    }
    if "status" in existing_columns:
        return
    op.add_column(
        "skills",
        sa.Column("status", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "skills",
        sa.Column(
            "redirect_target_skill_id",
            sa.String(length=36),
            nullable=True,
        ),
    )
    op.execute("UPDATE skills SET status = 'active' WHERE status IS NULL")
    with op.batch_alter_table("skills") as batch_op:
        batch_op.alter_column("status", nullable=False)
        batch_op.create_check_constraint(
            "ck_skills_status_allowed",
            "status in ('active', 'redirected', 'inactive')",
        )
        batch_op.create_foreign_key(
            "fk_skills_redirect_target_skill_id",
            "skills",
            ["redirect_target_skill_id"],
            ["id"],
        )
        batch_op.create_index("ix_skills_status", ["status"], unique=False)
        batch_op.create_index(
            "ix_skills_redirect_target_skill_id",
            ["redirect_target_skill_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("skills") as batch_op:
        batch_op.drop_index("ix_skills_redirect_target_skill_id")
        batch_op.drop_index("ix_skills_status")
        batch_op.drop_constraint(
            "fk_skills_redirect_target_skill_id",
            type_="foreignkey",
        )
        batch_op.drop_constraint("ck_skills_status_allowed", type_="check")
        batch_op.drop_column("redirect_target_skill_id")
        batch_op.drop_column("status")
