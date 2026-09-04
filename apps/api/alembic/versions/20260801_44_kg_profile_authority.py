"""Reference KG-owned graph versions without a main-system foreign key.

Revision ID: 20260801_44
Revises: 20260731_43
"""

from alembic import op


revision = "20260801_44"
down_revision = "20260731_43"
branch_labels = None
depends_on = None


NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"
}


def upgrade() -> None:
    with op.batch_alter_table(
        "trend_reports", naming_convention=NAMING_CONVENTION
    ) as batch:
        batch.drop_constraint(
            "fk_trend_reports_graph_version_id_graph_versions", type_="foreignkey"
        )


def downgrade() -> None:
    with op.batch_alter_table("trend_reports") as batch:
        batch.create_foreign_key(
            "fk_trend_reports_graph_version_id_graph_versions",
            "graph_versions",
            ["graph_version_id"],
            ["id"],
        )
