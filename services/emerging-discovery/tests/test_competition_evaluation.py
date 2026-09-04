import json
import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


ROOT = Path(__file__).resolve().parents[1]
HEADERS = {"Authorization": "Bearer development-emerging-discovery-token-change-me"}


def _dataset() -> dict:
    fixed = json.loads(
        (ROOT / "evaluation" / "discovery-competition-fixed.v1.json").read_text(
            encoding="utf-8"
        )
    )
    return fixed["cases"][0]


def _request(dataset: dict, extra: tuple[str, ...] = ()) -> dict:
    keys = (
        "contract_version",
        "request_id",
        "algorithm",
        "snapshots",
        "position_references",
        "config",
        *extra,
    )
    request = {key: dataset["input"][key] for key in keys}
    request["config"] = {
        **request.get("config", {}),
        "dataset_id": "emerging-discovery-full-temporal-v1",
    }
    request["snapshots"] = [
        {
            **snapshot,
            "content_hash": "sha256:" + hashlib.sha256(
                str(snapshot["jd_id"]).encode()
            ).hexdigest(),
            "structured_data": {
                **snapshot["structured_data"],
                "source_record_id": f"{snapshot['jd_id']}-source",
            },
        }
        for snapshot in request["snapshots"]
    ]
    request["time_windows"] = dataset["windows"]
    return request


def test_fixed_dataset_runs_formal_discovery_and_quantified_comparison():
    dataset = _dataset()
    with TestClient(app) as client:
        discovery = client.post(
            "/api/v1/discovery-runs", json=_request(dataset), headers=HEADERS
        )
        assert discovery.status_code == 201
        result = discovery.json()["data"]
        assert result["status"] == "succeeded"
        assert len(result["clusters"]) == 7
        assert result["run_context"]["algorithm"]["requested_algorithm"] == "emerge_v3_2"

        data_cluster = next(
            item
            for item in result["clusters"]
            if set(item["representative_jd_ids"]) == {"data-01", "data-02", "data-03"}
        )
        agent_clusters = [
            item
            for item in result["clusters"]
            if set(item["representative_jd_ids"])
            <= set(dataset["expected"]["positive_candidate_jd_ids"])
        ]
        assert len(agent_clusters) == 6
        assert all(item["sample_count"] == 1 for item in agent_clusters)
        assert all(
            item["merge_basis"]["rule"] == "exact_frozen_occupation_key"
            for item in result["clusters"]
        )

        members = client.get(
            f"/api/v1/clusters/{data_cluster['cluster_id']}/memberships", headers=HEADERS
        )
        assert members.status_code == 200
        member_rows = members.json()["data"]["memberships"]
        assert {item["source_jd_id"] for item in member_rows} == set(
            ("data-01", "data-02", "data-03")
        )
        assert len({item["window_id"] for item in member_rows}) == 3

        evaluation = client.post(
            "/api/v1/discovery-evaluations",
            json={"dataset_version": "discovery-competition-fixed.v1"},
            headers=HEADERS,
        )
        assert evaluation.status_code == 200

    report = evaluation.json()["data"]
    case = report["cases"][0]
    metrics = case["metric_results"]
    assert metrics["baseline"]["overall_precision_at_k"] == 0.0
    assert metrics["multi_view"]["overall_precision_at_k"] == 1.0
    assert case["model_results"]["multi_view"]["overall"]["stability"][
        "stability_score"
    ] == 1.0
    assert [item["jd_id"] for item in metrics["multi_view"]["overall_top_k"]] == [
        "agent-01",
        "agent-02",
    ]
    assert metrics["multi_view"]["false_positive_jd_ids"] == []
    assert "两名维护者" in case["human_expected"]["annotation_note"]
