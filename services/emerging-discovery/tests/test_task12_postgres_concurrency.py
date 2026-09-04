from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.infrastructure.models import (
    AlgorithmConfigSnapshot,
    Cluster,
    ClusterLineage,
    ClusterMembership,
    DiscoveryRun,
    GerminationAssessment,
    InputSnapshot,
)
from app.main import app
from tests.runtime_database import SessionLocal, engine
from tests.test_api import HEADERS, _payload


def _post(payload: dict):
    with TestClient(app) as client:
        return client.post("/api/v1/discovery-runs", json=payload, headers=HEADERS)


def _table_count(session, model: type) -> int:
    return session.scalar(select(func.count()).select_from(model))


def _assert_complete_result(data: dict) -> None:
    assert data["status"] == "succeeded"
    assert data["clusters"]
    assert data["run_context"]["algorithm"]["algorithm_version"]
    assert data["run_context"]["config"]
    assert all(cluster["germination_assessment"] for cluster in data["clusters"])


def test_repeated_request_id_returns_the_same_complete_run_in_postgresql():
    assert engine.dialect.name == "postgresql"
    first_payload = _payload("task12-same-request")
    repeated_payload = _payload("task12-same-request")

    first = _post(first_payload)
    repeated = _post(repeated_payload)
    assert first.status_code == 201
    assert repeated.status_code == 201
    first_data = first.json()["data"]
    repeated_data = repeated.json()["data"]
    _assert_complete_result(first_data)
    assert repeated_data == first_data

    with SessionLocal() as session:
        cluster_count = _table_count(session, Cluster)
        assert _table_count(session, DiscoveryRun) == 1
        assert _table_count(session, InputSnapshot) == len(first_payload["snapshots"])
        assert _table_count(session, AlgorithmConfigSnapshot) == 1
        assert cluster_count == len(first_data["clusters"])
    assert _table_count(session, ClusterMembership) == len(
        first_payload["snapshots"]
    )
    assert _table_count(session, GerminationAssessment) == cluster_count
    assert _table_count(session, ClusterLineage) == len(first_data["lineages"])
