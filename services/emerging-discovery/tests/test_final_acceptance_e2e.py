from __future__ import annotations

import json
import hashlib
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)
HEADERS = {"Authorization": "Bearer development-emerging-discovery-token-change-me"}
FIXTURE = Path(__file__).parents[1] / "examples" / "final_discovery_fixture.json"


def _fixture() -> dict:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for run in fixture["runs"]:
        run["config"] = {
            **run.get("config", {}),
            "dataset_id": "emerging-discovery-full-temporal-v1",
        }
        for snapshot in run["snapshots"]:
            snapshot["content_hash"] = "sha256:" + hashlib.sha256(
                str(snapshot["jd_id"]).encode()
            ).hexdigest()
            snapshot["structured_data"]["source_record_id"] = (
                f"{snapshot['jd_id']}-source"
            )
    return fixture


def _assert_same_result(left: dict, right: dict) -> None:
    for name in ("created_at", "completed_at"):
        assert datetime.fromisoformat(left[name]) == datetime.fromisoformat(right[name])
    assert {
        key: value for key, value in left.items() if key not in {"created_at", "completed_at"}
    } == {key: value for key, value in right.items() if key not in {"created_at", "completed_at"}}


def test_complete_evidence_driven_discovery_chain_is_query_consistent():
    fixture = _fixture()
    first = client.post("/api/v1/discovery-runs", json=fixture["runs"][0], headers=HEADERS)
    assert first.status_code == 201
    second = client.post("/api/v1/discovery-runs", json=fixture["runs"][1], headers=HEADERS)
    assert second.status_code == 201
    created = second.json()["data"]

    report = created["input_quality_report"]
    assert report["raw_jd_count"] == 5
    assert report["valid_jd_count"] == 5
    assert report["deduplicated_jd_count"] == 5
    assert report["duplicate_jd_count"] == 0
    assert report["excluded_samples"] == []
    assert created["lineages"]
    assert "continue" in {item["relation_type"] for item in created["lineages"]}

    cluster = next(
        item for item in created["clusters"] if "demo-jd-4" in item["representative_jd_ids"]
    )
    assert (
        cluster["standard_position_comparison"]["nearest_standard_position"] == "standard-backend"
    )
    assert (
        len(cluster["germination_assessment"]["evidence_package"]["emergence_index"]["dimensions"])
        == 7
    )
    definition = cluster["generated_definition"]
    assert definition["position_name"] == "RAG 应用工程师"
    assert definition["core_responsibilities"]
    assert definition["required_skills"]
    assert definition["field_evidence"]["position_name"]["evidence_ids"]

    queried = client.get(f"/api/v1/discovery-runs/{created['run_id']}", headers=HEADERS)
    assert queried.status_code == 200
    _assert_same_result(queried.json()["data"], created)

    idempotent = client.post("/api/v1/discovery-runs", json=fixture["runs"][1], headers=HEADERS)
    assert idempotent.status_code == 201
    _assert_same_result(idempotent.json()["data"], created)


def test_missing_required_snapshot_trace_is_rejected():
    payload = deepcopy(_fixture()["runs"][0])
    payload["request_id"] = "final-insufficient-evidence"
    snapshot = payload["snapshots"][0]
    snapshot["publish_date"] = None
    snapshot["structured_data"] = {
        "responsibilities": [],
        "required_skills": [],
        "bonus_skills": [],
        "business_scenarios": [],
    }
    payload["snapshots"] = [snapshot]
    payload["position_references"] = [
        {
            "position_id": "reference-without-skills",
            "graph_version_id": "graph-v1",
            "required_skills": [],
        }
    ]

    response = client.post("/api/v1/discovery-runs", json=payload, headers=HEADERS)
    assert response.status_code == 422
    assert "publish_date" in str(response.json()["data"])


def test_local_semantic_unavailability_is_explicit_in_acceptance_chain():
    payload = _fixture()["runs"][0]
    payload["request_id"] = "final-semantic-unavailable"
    payload["comparison_algorithms"] = ["semantic_agglomerative"]
    response = client.post("/api/v1/discovery-comparisons", json=payload, headers=HEADERS)
    assert response.status_code == 503
    assert "fallback" not in response.json()["data"]
