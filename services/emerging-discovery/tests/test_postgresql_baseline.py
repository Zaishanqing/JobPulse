from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from tests.runtime_database import engine


def test_postgresql_baseline_has_required_constraints_and_indexes():
    assert engine.dialect.name == "postgresql"
    inspector = inspect(engine)

    snapshot_uniques = {
        item["name"] for item in inspector.get_unique_constraints("input_snapshots")
    }
    cluster_uniques = {item["name"] for item in inspector.get_unique_constraints("clusters")}
    assert "uq_input_snapshots_run_source_jd" in snapshot_uniques
    assert "uq_clusters_run_key" in cluster_uniques

    run_indexes = {item["name"] for item in inspector.get_indexes("discovery_runs")}
    assert {"ix_discovery_runs_request_id", "ix_discovery_runs_time_window"} <= run_indexes
    for table in ("input_snapshots", "clusters", "cluster_lineages"):
        assert any(
            "run_id" in item["column_names"] for item in inspector.get_indexes(table)
        )


def test_snapshot_and_cluster_unique_constraints_reject_duplicates():
    now = datetime.now(timezone.utc)
    run_values = {
        "id": "run-1",
        "created_at": now,
        "request_id": "request-1",
        "status": "succeeded",
        "algorithm_version": "test",
        "formula_version": "test",
        "completed_at": now,
    }
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(
            text(
                "INSERT INTO discovery_runs "
                "(id, created_at, request_id, status, algorithm_version, formula_version, "
                "completed_at) VALUES "
                "(:id, :created_at, :request_id, :status, "
                ":algorithm_version, :formula_version, :completed_at)"
            ),
            run_values,
        )

        snapshot_values = {
            "created_at": now,
            "run_id": "run-1",
            "source_jd_id": "jd-1",
            "payload": "{}",
        }
        connection.execute(
            text(
                "INSERT INTO input_snapshots "
                "(id, created_at, run_id, source_jd_id, window_id, input_version, "
                "schema_version, payload) "
                "VALUES ('snapshot-1', :created_at, :run_id, :source_jd_id, 'w1', 'v1', 'v2', "
                "CAST(:payload AS jsonb))"
            ),
            snapshot_values,
        )
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                text(
                    "INSERT INTO input_snapshots "
                    "(id, created_at, run_id, source_jd_id, window_id, input_version, "
                    "schema_version, payload) "
                    "VALUES ('snapshot-2', :created_at, :run_id, :source_jd_id, 'w1', 'v2', 'v2', "
                    "CAST(:payload AS jsonb))"
                ),
                snapshot_values,
            )

        cluster_values = {"created_at": now, "run_id": "run-1", "cluster_key": "cluster-1"}
        cluster_sql = text(
            "INSERT INTO clusters "
            "(id, created_at, run_id, cluster_key, cluster_name, sample_count, core_skills, "
            "representative_titles, representative_members, core_responsibilities, "
            "semantic_centroid, algorithm_sources, merge_basis, stability_score, growth_score, "
            "distance_from_existing_positions, feature_summary) VALUES "
            "(:id, :created_at, :run_id, :cluster_key, 'cluster', 1, '[]'::jsonb, '[]'::jsonb, "
            "'[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '{}'::jsonb, "
            "0.5, 0.5, 0.5, '{}'::jsonb)"
        )
        connection.execute(cluster_sql, {**cluster_values, "id": "cluster-1"})
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(cluster_sql, {**cluster_values, "id": "cluster-2"})
        transaction.rollback()
