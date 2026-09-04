"""prevent synthetic demo facts from becoming authoritative

Revision ID: 0018_demo_dataset_isolation
Revises: 0017_async_build_impact_review
"""

from alembic import op


revision = "0018_demo_dataset_isolation"
down_revision = "0017_async_build_impact_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("jd_documents") as batch:
        batch.create_check_constraint(
            "ck_jd_documents_synthetic_not_authoritative",
            "NOT (is_synthetic = true AND fact_authority = 'authoritative')",
        )


def downgrade() -> None:
    with op.batch_alter_table("jd_documents") as batch:
        batch.drop_constraint(
            "ck_jd_documents_synthetic_not_authoritative", type_="check"
        )
