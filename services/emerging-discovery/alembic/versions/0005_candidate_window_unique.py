"""Enforce one lifecycle observation per candidate per window."""

import sqlalchemy as sa

from alembic import op


revision = "0005_candidate_window_unique"
down_revision = "0004_identity_resolution_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("emerging discovery supports PostgreSQL only")
    duplicate = op.get_bind().execute(
        sa.text(
            "SELECT candidate_id, window_id "
            "FROM candidate_cluster_observations "
            "GROUP BY candidate_id, window_id "
            "HAVING COUNT(*) > 1 "
            "LIMIT 1"
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "duplicate (candidate_id, window_id) observations exist; "
            "resolve before adding uq_candidate_observation_window"
        )
    op.create_unique_constraint(
        "uq_candidate_observation_window",
        "candidate_cluster_observations",
        ["candidate_id", "window_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_candidate_observation_window",
        "candidate_cluster_observations",
        type_="unique",
    )
