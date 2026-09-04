"""Add remote skill trend report lineage.

Revision ID: 20260730_42
Revises: 20260730_41
"""

from alembic import op
import sqlalchemy as sa


revision = "20260730_42"
down_revision = "20260730_41"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = (
        sa.Column("provider_run_id", sa.String(80), nullable=True),
        sa.Column("input_fingerprint", sa.String(64), nullable=True),
        sa.Column("algorithm_version", sa.String(128), nullable=True),
        sa.Column("formula_version", sa.String(128), nullable=True),
        sa.Column("skill_catalog_version", sa.String(128), nullable=True),
        sa.Column("source_coverage", sa.Float(), nullable=True),
        sa.Column("missing_sources", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("quality_flags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("evidence_references", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("unresolved_terms", sa.JSON(), nullable=False, server_default="[]"),
    )
    inspector = sa.inspect(op.get_bind())
    existing_columns = {item["name"] for item in inspector.get_columns("trend_reports")}
    missing_columns = [column for column in columns if column.name not in existing_columns]
    unique_columns = {
        tuple(item.get("column_names") or ())
        for item in inspector.get_unique_constraints("trend_reports")
    }
    indexes = inspector.get_indexes("trend_reports")
    unique_columns.update(
        tuple(item.get("column_names") or ()) for item in indexes if item.get("unique")
    )
    existing_indexes = {item["name"] for item in indexes}
    unique_key = ("provider_run_id", "position_id", "graph_version_id")
    needs_unique = unique_key not in unique_columns
    needs_index = "ix_trend_reports_provider_run_id" not in existing_indexes
    if missing_columns or needs_unique or needs_index:
        with op.batch_alter_table("trend_reports") as batch:
            for column in missing_columns:
                batch.add_column(column)
            if needs_unique:
                batch.create_unique_constraint(
                    "uq_trend_report_provider_position_graph",
                    list(unique_key),
                )
            if needs_index:
                batch.create_index("ix_trend_reports_provider_run_id", ["provider_run_id"])


def downgrade() -> None:
    with op.batch_alter_table("trend_reports") as batch:
        batch.drop_index("ix_trend_reports_provider_run_id")
        batch.drop_constraint("uq_trend_report_provider_position_graph", type_="unique")
        for name in (
            "unresolved_terms", "evidence_references", "quality_flags",
            "missing_sources", "source_coverage", "skill_catalog_version",
            "formula_version", "algorithm_version", "input_fingerprint",
            "provider_run_id",
        ):
            batch.drop_column(name)
