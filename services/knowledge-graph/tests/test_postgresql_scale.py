from __future__ import annotations

import os

import pytest

from scripts.run_postgres_scale import _require_disposable_postgres, run
from scripts.verify_postgres_backup_restore import _validate, verify


PERF_URL = os.getenv("KG_PERF_POSTGRES_URL")
RESTORE_URL = os.getenv("KG_RESTORE_POSTGRES_URL")


def test_scale_runner_rejects_non_postgresql_and_non_disposable_databases():
    with pytest.raises(RuntimeError, match="requires PostgreSQL"):
        _require_disposable_postgres("sqlite:///performance.db")
    with pytest.raises(RuntimeError, match="disposable"):
        _require_disposable_postgres("postgresql+psycopg://kg:x@db/production")


def test_backup_restore_rejects_same_database():
    url = "postgresql+psycopg://kg:x@db/knowledge_graph_test"
    with pytest.raises(RuntimeError, match="must be different"):
        _validate(url, url)


@pytest.mark.skipif(not PERF_URL, reason="KG_PERF_POSTGRES_URL is not configured")
def test_postgresql_100k_scale_and_concurrency():
    assert PERF_URL is not None
    result = run(PERF_URL, 100_000, 10_000, 4)
    assert result["counts"] == {
        "jd_facts": 100_000,
        "relations": 10_000,
        "positions": 4,
    }
    assert all(result["assertions"].values())


@pytest.mark.skipif(
    not PERF_URL or not RESTORE_URL,
    reason="KG_PERF_POSTGRES_URL and KG_RESTORE_POSTGRES_URL are required",
)
def test_postgresql_backup_restore_fidelity():
    assert PERF_URL is not None and RESTORE_URL is not None
    result = verify(PERF_URL, RESTORE_URL)
    assert result["schema_counts_and_version_hashes_equal"] is True
