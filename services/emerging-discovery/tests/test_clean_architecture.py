from __future__ import annotations

import ast
import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ports.records import RunRecord
from app.bootstrap.application import create_app
from tests.runtime_database import SessionLocal
from app.infrastructure.models import DiscoveryRun
from app.infrastructure.repositories import (
    SqlAlchemyClusterRepository,
    SqlAlchemyDiscoveryUnitOfWork,
    SqlAlchemyRunRepository,
    SqlAlchemySnapshotRepository,
)

ROOT = Path(__file__).parents[1]
HEADERS = {"Authorization": "Bearer development-emerging-discovery-token-change-me"}


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.append(node.module)
    return result


def test_application_import_graph_points_only_to_domain_ports_and_stdlib():
    forbidden = (
        "fastapi",
        "pydantic",
        "sqlalchemy",
        "app.api",
        "app.bootstrap",
        "app.infrastructure",
    )
    for path in (ROOT / "app" / "application").glob("*.py"):
        assert not any(name.startswith(forbidden) for name in _imports(path)), path


def test_normal_discovery_repositories_have_no_commit_or_rollback_calls():
    for repository in (
        SqlAlchemyRunRepository,
        SqlAlchemySnapshotRepository,
        SqlAlchemyClusterRepository,
    ):
        tree = ast.parse(inspect.getsource(repository))
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert not ({"commit", "rollback"} & calls), repository


def _run(run_id: str) -> RunRecord:
    return RunRecord(
        id=run_id,
        request_id=f"request-{run_id}",
        status="succeeded",
        algorithm_version="test-v1",
        formula_version="test-v1",
        time_window_start=None,
        time_window_end=None,
        completed_at=datetime.now(timezone.utc),
    )


def test_real_sqlalchemy_uow_commits_and_rolls_back():
    with SessionLocal() as db:
        uow = SqlAlchemyDiscoveryUnitOfWork(db)
        uow.runs.add(_run("commit"), {"id": "cfg-commit", "config": {}})
        uow.commit()
    with SessionLocal() as db:
        assert db.get(DiscoveryRun, "commit") is not None
        uow = SqlAlchemyDiscoveryUnitOfWork(db)
        uow.runs.add(
            _run("rollback"), {"id": "cfg-rollback", "config": {}}
        )
        uow.rollback()
    with SessionLocal() as db:
        assert db.get(DiscoveryRun, "rollback") is None


def _versioned_payload() -> dict:
    return {
        "contract_version": "discovery.v2",
        "request_id": "version-test",
        "algorithm": "emerge_v3_2",
        "time_windows": [
            {"window_id": "w1", "start": "2026-01-01", "end": "2026-01-31"},
            {"window_id": "w2", "start": "2026-02-01", "end": "2026-02-28"},
            {"window_id": "w3", "start": "2026-03-01", "end": "2026-03-31"},
        ],
        "snapshots": [
            {
                "source_fact_id": "fact-1",
                "source_fact_version": "1",
                "jd_id": "jd-1",
                "schema_version": "v2",
                "review_status": "published",
                "consumption_path": "published",
                "title": "Python 工程师",
                "publish_date": "2026-01-01",
                "content_hash": "sha256:" + "1" * 64,
                "structured_data": {
                    "responsibilities": ["建设数据处理服务"],
                    "required_skills": [{"raw_skill": "Python"}],
                    "bonus_skills": [],
                    "business_scenarios": [],
                    "source_record_id": "source-1",
                },
            },
            {
                "source_fact_id": "fact-3",
                "source_fact_version": "1",
                "jd_id": "jd-3",
                "schema_version": "v2",
                "review_status": "published",
                "consumption_path": "published",
                "title": "Python 工程师",
                "publish_date": "2026-03-01",
                "content_hash": "sha256:" + "3" * 64,
                "structured_data": {
                    "responsibilities": ["建设数据处理服务"],
                    "required_skills": [{"raw_skill": "Python"}],
                    "bonus_skills": [],
                    "business_scenarios": [],
                    "source_record_id": "source-3",
                },
            },
            {
                "source_fact_id": "fact-2",
                "source_fact_version": "1",
                "jd_id": "jd-2",
                "schema_version": "v2",
                "review_status": "published",
                "consumption_path": "published",
                "title": "Python 工程师",
                "publish_date": "2026-02-01",
                "content_hash": "sha256:" + "2" * 64,
                "structured_data": {
                    "responsibilities": ["建设数据处理服务"],
                    "required_skills": [{"raw_skill": "Python"}],
                    "bonus_skills": [],
                    "business_scenarios": [],
                    "source_record_id": "source-2",
                },
            },
        ],
        "position_references": [
            {
                "position_id": "formal",
                "graph_version_id": "graph-v1",
                "required_skills": [{"raw_skill": "Java"}],
            }
        ],
        "config": {"dataset_id": "emerging-discovery-full-temporal-v1"},
    }


@pytest.mark.parametrize("version", ["discovery.v1", "discovery.v999"])
def test_unknown_and_incompatible_contract_versions_are_stable_422(version):
    client = TestClient(create_app())
    payload = _versioned_payload()
    payload["contract_version"] = version
    response = client.post("/api/v1/discovery-runs", json=payload, headers=HEADERS)
    assert response.status_code == 422
    assert response.json()["code"] == 422
    assert "contract_version" in str(response.json()["data"])


def test_missing_version_is_rejected_without_legacy_adapter():
    client = TestClient(create_app())
    payload = _versioned_payload()
    payload.pop("contract_version")
    assert client.post("/api/v1/discovery-runs", json=payload, headers=HEADERS).status_code == 422
    payload.pop("algorithm")
    payload["snapshots"][0]["review_status"] = "approved"
    payload["snapshots"][0].pop("consumption_path")
    payload["snapshots"][1]["review_status"] = "approved"
    payload["snapshots"][1].pop("consumption_path")
    assert client.post("/api/v1/discovery-runs", json=payload, headers=HEADERS).status_code == 422


def test_reviewed_legacy_and_published_paths_are_distinct_and_truthful():
    client = TestClient(create_app())
    reviewed = _versioned_payload()
    reviewed["request_id"] = "reviewed-path"
    reviewed["snapshots"][0]["review_status"] = "reviewed"
    reviewed["snapshots"][0]["consumption_path"] = "legacy_reviewed"
    reviewed["snapshots"][1]["review_status"] = "reviewed"
    reviewed["snapshots"][1]["consumption_path"] = "legacy_reviewed"
    assert client.post("/api/v1/discovery-runs", json=reviewed, headers=HEADERS).status_code == 201
    reviewed["snapshots"][0]["consumption_path"] = "published"
    reviewed["snapshots"][1]["consumption_path"] = "published"
    assert client.post("/api/v1/discovery-runs", json=reviewed, headers=HEADERS).status_code == 422


def test_algorithm_field_selects_the_emerge_v3_2_execution_profile():
    from app.application.discovery_identity import normalize_algorithm

    selected = normalize_algorithm("emerge_v3_2")
    assert selected.requested_name == "emerge_v3_2"
    assert selected.canonical_name == "emerge_v3_2"
    assert selected.similarity_threshold.value == 0.72


def test_request_id_reuse_with_different_payload_returns_contract_conflict():
    client = TestClient(create_app())
    payload = _versioned_payload()
    first = client.post("/api/v1/discovery-runs", json=payload, headers=HEADERS)
    assert first.status_code == 201
    payload["config"]["conflict_marker"] = "changed"
    response = client.post("/api/v1/discovery-runs", json=payload, headers=HEADERS)
    assert response.status_code == 409
    assert "different payload" in response.json()["message"]
    assert response.json()["code"] == 409
