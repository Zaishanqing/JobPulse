"""reconcile legacy document schema in persistent deployments

Revision ID: 0003_reconcile_legacy_document_schema
Revises: 0002_trusted_graph_workflow
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_reconcile_legacy_document_schema"
down_revision = "0002_trusted_graph_workflow"
branch_labels = None
depends_on = None

def upgrade():
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("jd_documents")}
    if "enterprise_name" not in columns:
        op.add_column("jd_documents", sa.Column("enterprise_name", sa.String(160), nullable=True))

def downgrade():
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("jd_documents")}
    if "enterprise_name" in columns:
        op.drop_column("jd_documents", "enterprise_name")
