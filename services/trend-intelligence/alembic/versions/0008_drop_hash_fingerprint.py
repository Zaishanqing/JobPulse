"""Reconcile schema drift from the hash/fingerprint removal refactor.

Commit 95d81e52 removed business hash/fingerprint columns from the ORM models
(content_hash, input_fingerprint, request_fingerprint, response_hash) and
renamed raw_snapshots.content_hash -> source_version, but no migration was
written. Databases created before that refactor still carry the stale columns
and are missing the new ones, which breaks the acquisition insert path.

Revision ID: 0008_drop_hash_fingerprint
Revises: 0007_event_cluster_id_length
"""

import sqlalchemy as sa
from alembic import op


revision = "0008_drop_hash_fingerprint"
down_revision = "0007_event_cluster_id_length"
branch_labels = None
depends_on = None


def _columns(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def _constraints(bind, table: str) -> set[str]:
    inspector = sa.inspect(bind)
    return {uc["name"] for uc in inspector.get_unique_constraints(table)}


def _indexes(bind, table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()

    # raw_snapshots: content_hash -> source_version
    raw_columns = _columns(bind, "raw_snapshots")
    if "uq_raw_snapshot_identity" in _constraints(bind, "raw_snapshots"):
        op.drop_constraint("uq_raw_snapshot_identity", "raw_snapshots", type_="unique")
    if "content_hash" in raw_columns:
        op.drop_column("raw_snapshots", "content_hash")
    if "source_version" not in raw_columns:
        op.add_column(
            "raw_snapshots",
            sa.Column("source_version", sa.String(length=128), nullable=False),
        )
    if "ix_raw_snapshots_source_version" not in _indexes(bind, "raw_snapshots"):
        op.create_index(
            "ix_raw_snapshots_source_version", "raw_snapshots", ["source_version"]
        )
    op.create_unique_constraint(
        "uq_raw_snapshot_identity",
        "raw_snapshots",
        ["source_id", "external_id", "source_version"],
    )

    # source_snapshots: drop content_hash, re-key identity on (source, external_id, source_version)
    if "uq_source_snapshot_identity" in _constraints(bind, "source_snapshots"):
        op.drop_constraint("uq_source_snapshot_identity", "source_snapshots", type_="unique")
    if "content_hash" in _columns(bind, "source_snapshots"):
        op.drop_column("source_snapshots", "content_hash")
    op.create_unique_constraint(
        "uq_source_snapshot_identity",
        "source_snapshots",
        ["source", "external_id", "source_version"],
    )

    # acquisition_bundles: drop content_hash
    if "content_hash" in _columns(bind, "acquisition_bundles"):
        op.drop_column("acquisition_bundles", "content_hash")

    # analysis_runs / backtest_runs: drop input_fingerprint
    if "input_fingerprint" in _columns(bind, "analysis_runs"):
        op.drop_column("analysis_runs", "input_fingerprint")
    if "input_fingerprint" in _columns(bind, "backtest_runs"):
        op.drop_column("backtest_runs", "input_fingerprint")

    # source_replay_cache: request_fingerprint/response_hash -> request_id/run_id
    replay_columns = _columns(bind, "source_replay_cache")
    if "request_fingerprint" in replay_columns:
        op.drop_column("source_replay_cache", "request_fingerprint")
    if "response_hash" in replay_columns:
        op.drop_column("source_replay_cache", "response_hash")
    if "request_id" not in replay_columns:
        op.add_column(
            "source_replay_cache",
            sa.Column("request_id", sa.String(length=128), nullable=False),
        )
    if "run_id" not in replay_columns:
        op.add_column(
            "source_replay_cache",
            sa.Column("run_id", sa.String(length=36), nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()

    raw_columns = _columns(bind, "raw_snapshots")
    if "uq_raw_snapshot_identity" in _constraints(bind, "raw_snapshots"):
        op.drop_constraint("uq_raw_snapshot_identity", "raw_snapshots", type_="unique")
    if "source_version" in raw_columns:
        op.drop_index("ix_raw_snapshots_source_version", table_name="raw_snapshots")
        op.drop_column("raw_snapshots", "source_version")
    if "content_hash" not in raw_columns:
        op.add_column(
            "raw_snapshots",
            sa.Column("content_hash", sa.String(length=64), nullable=False),
        )
    op.create_unique_constraint(
        "uq_raw_snapshot_identity",
        "raw_snapshots",
        ["source_id", "external_id", "content_hash"],
    )

    if "uq_source_snapshot_identity" in _constraints(bind, "source_snapshots"):
        op.drop_constraint("uq_source_snapshot_identity", "source_snapshots", type_="unique")
    if "content_hash" not in _columns(bind, "source_snapshots"):
        op.add_column(
            "source_snapshots",
            sa.Column("content_hash", sa.String(length=64), nullable=False),
        )
    op.create_unique_constraint(
        "uq_source_snapshot_identity",
        "source_snapshots",
        ["source", "external_id", "source_version", "content_hash"],
    )

    if "content_hash" not in _columns(bind, "acquisition_bundles"):
        op.add_column(
            "acquisition_bundles",
            sa.Column("content_hash", sa.String(length=64), nullable=False),
        )

    if "input_fingerprint" not in _columns(bind, "analysis_runs"):
        op.add_column(
            "analysis_runs",
            sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        )
    if "input_fingerprint" not in _columns(bind, "backtest_runs"):
        op.add_column(
            "backtest_runs",
            sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        )

    replay_columns = _columns(bind, "source_replay_cache")
    if "request_id" in replay_columns:
        op.drop_column("source_replay_cache", "request_id")
    if "run_id" in replay_columns:
        op.drop_column("source_replay_cache", "run_id")
    if "request_fingerprint" not in replay_columns:
        op.add_column(
            "source_replay_cache",
            sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        )
    if "response_hash" not in replay_columns:
        op.add_column(
            "source_replay_cache",
            sa.Column("response_hash", sa.String(length=64), nullable=False),
        )
