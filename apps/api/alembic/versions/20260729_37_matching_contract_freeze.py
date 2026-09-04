"""freeze matching Contract lineage

Revision ID: 20260729_37
Revises: 20260728_36
"""

import sqlalchemy as sa
from alembic import op


revision = "20260729_37"
down_revision = "20260728_36"
branch_labels = None
depends_on = None


_COLUMNS = (
    ("schema_version", sa.String(64), "legacy.v0"),
    ("access_scope", sa.String(200), "legacy-unspecified"),
    ("source_version", sa.String(512), "legacy-unspecified"),
    ("taxonomy_version", sa.String(512), "legacy-unspecified"),
    ("graph_version", sa.String(255), "legacy-unspecified"),
    ("algorithm_version", sa.String(255), "legacy-unspecified"),
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table in ("matching_service_references", "matching_submission_intents"):
        if table not in inspector.get_table_names():
            continue
        existing = {item["name"] for item in inspector.get_columns(table)}
        with op.batch_alter_table(table) as batch:
            for name, type_, default in _COLUMNS:
                if name not in existing:
                    batch.add_column(
                        sa.Column(name, type_, nullable=False, server_default=default)
                    )
            if table == "matching_service_references":
                for name in ("cv_profile_fingerprint", "position_profile_fingerprint"):
                    if name not in existing:
                        batch.add_column(
                            sa.Column(name, sa.String(64), nullable=False, server_default="")
                        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table in ("matching_submission_intents", "matching_service_references"):
        if table not in inspector.get_table_names():
            continue
        existing = {item["name"] for item in inspector.get_columns(table)}
        names = [item[0] for item in _COLUMNS]
        if table == "matching_service_references":
            names += ["cv_profile_fingerprint", "position_profile_fingerprint"]
        with op.batch_alter_table(table) as batch:
            for name in reversed(names):
                if name in existing:
                    batch.drop_column(name)
