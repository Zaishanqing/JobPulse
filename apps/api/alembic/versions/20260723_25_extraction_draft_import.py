"""add extraction bundle lineage to JD drafts

Revision ID: 20260723_25
Revises: 20260723_24
Create Date: 2026-07-23
"""

from alembic import op
import sqlalchemy as sa


revision = "20260723_25"
down_revision = "20260723_24"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("job_descriptions") as batch_op:
        batch_op.add_column(sa.Column("source_jd_id", sa.String(36), nullable=True))
        batch_op.add_column(
            sa.Column("source_jd_version_id", sa.String(36), nullable=True)
        )
        batch_op.add_column(
            sa.Column("extraction_task_id", sa.String(36), nullable=True)
        )
        batch_op.add_column(
            sa.Column("source_document_id", sa.String(128), nullable=True)
        )
        batch_op.add_column(
            sa.Column("extraction_bundle_version", sa.String(64), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_job_descriptions_source_jd_id",
            "source_jds",
            ["source_jd_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_job_descriptions_source_jd_version_id",
            "source_jd_versions",
            ["source_jd_version_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_job_descriptions_extraction_task_id",
            "extraction_tasks",
            ["extraction_task_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index(
            "ix_job_descriptions_extraction_task_id",
            ["extraction_task_id"],
            unique=True,
        )
        batch_op.create_index(
            "ix_job_descriptions_source_jd_id", ["source_jd_id"], unique=False
        )
        batch_op.create_index(
            "ix_job_descriptions_source_jd_version_id",
            ["source_jd_version_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_job_descriptions_source_document_id",
            ["source_document_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("job_descriptions") as batch_op:
        batch_op.drop_index("ix_job_descriptions_source_document_id")
        batch_op.drop_index("ix_job_descriptions_source_jd_version_id")
        batch_op.drop_index("ix_job_descriptions_source_jd_id")
        batch_op.drop_index("ix_job_descriptions_extraction_task_id")
        batch_op.drop_constraint(
            "fk_job_descriptions_extraction_task_id", type_="foreignkey"
        )
        batch_op.drop_constraint(
            "fk_job_descriptions_source_jd_version_id", type_="foreignkey"
        )
        batch_op.drop_constraint(
            "fk_job_descriptions_source_jd_id", type_="foreignkey"
        )
        batch_op.drop_column("extraction_bundle_version")
        batch_op.drop_column("source_document_id")
        batch_op.drop_column("extraction_task_id")
        batch_op.drop_column("source_jd_version_id")
        batch_op.drop_column("source_jd_id")
