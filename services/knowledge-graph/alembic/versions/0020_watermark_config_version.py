"""replace normalized watermark config hash with its explicit version"""

from alembic import op
import sqlalchemy as sa

revision = "0020_watermark_config_version"
down_revision = "0019_graph_source_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("build_input_watermarks") as batch:
        batch.add_column(
            sa.Column("config_version", sa.String(100), nullable=False, server_default="config-v1")
        )
        batch.drop_column("normalized_config_hash")


def downgrade() -> None:
    with op.batch_alter_table("build_input_watermarks") as batch:
        batch.add_column(sa.Column("normalized_config_hash", sa.String(64), nullable=False, server_default="legacy-removed"))
        batch.drop_column("config_version")
