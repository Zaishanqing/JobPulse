from calendar import monthrange
import hashlib

from fastapi.testclient import TestClient

from app.main import app
from tests.runtime_database import SessionLocal
from app.infrastructure.models import DiscoveryRun, InputSnapshot

client = TestClient(app)
HEADERS = {"Authorization": "Bearer development-emerging-discovery-token-change-me"}


def _payload(request_id: str = "request-1", *, month_offset: int = 0) -> dict:
    snapshots = []
    for index in range(3):
        month = index + 1 + month_offset
        year = 2026 + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        snapshots.append(
            {
                "source_fact_id": f"fact-{index}",
                "source_fact_version": "1",
                "jd_id": f"jd-{index}",
                "schema_version": "v2",
                "review_status": "published",
                "consumption_path": "published",
                "title": "大模型应用开发工程师",
                "source_name": "platform-a",
                "publish_date": f"{year}-{month:02d}-01",
                "content_hash": "sha256:" + hashlib.sha256(
                    f"{request_id}:{index}".encode()
                ).hexdigest(),
                "structured_data": {
                    "responsibilities": ["应用开发"],
                    "required_skills": [{"raw_skill": "RAG"}, {"raw_skill": "Python"}],
                    "bonus_skills": [],
                    "industry": "人工智能",
                    "business_scenarios": ["智能客服"],
                    "source_platform": "platform-a",
                    "source_record_id": f"{request_id}-source-{index}",
                },
            }
        )
    time_windows = []
    for index in range(3):
        month = index + 1 + month_offset
        year = 2026 + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        end_day = monthrange(year, month)[1]
        time_windows.append(
            {
                "window_id": f"{year}-{month:02d}",
                "start": f"{year}-{month:02d}-01",
                "end": f"{year}-{month:02d}-{end_day:02d}",
            }
        )
    return {
        "contract_version": "discovery.v2",
        "request_id": request_id,
        "algorithm": "emerge_v3_2",
        "time_windows": time_windows,
        "snapshots": snapshots,
        "position_references": [{
            "position_id": "formal-java",
            "graph_version_id": "graph-v1",
            "required_skills": [{"normalized_skill_id": "java"}],
        }],
        "config": {"dataset_id": "emerging-discovery-full-temporal-v1"},
    }


def test_health_and_discovery_contract():
    assert client.get("/health").json() == {
        "code": 0,
        "message": "success",
        "data": {"status": "healthy", "service": "emerging-discovery"},
    }
    response = client.post("/api/v1/discovery-runs", json=_payload(), headers=HEADERS)
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["status"] == "succeeded"
    assert data["input_quality_report"] == {
        "policy_version": "window-dedup-v1",
        "raw_jd_count": 3,
        "valid_jd_count": 3,
        "deduplicated_jd_count": 3,
        "duplicate_jd_count": 0,
        "enterprise_count": "unavailable",
        "source_count": 1,
        "time_coverage": {"start": "2026-01-01", "end": "2026-03-01"},
        "evidence_completeness_rate": "unavailable",
        "unresolved_skill_ratio": 1.0,
        "raw": {
            "jd_count": 3,
            "enterprise_count": "unavailable",
            "source_count": 1,
            "time_coverage": {"start": "2026-01-01", "end": "2026-03-01"},
            "evidence_completeness_rate": "unavailable",
            "unresolved_skill_ratio": 1.0,
        },
        "effective": {
            "jd_count": 3,
            "enterprise_count": "unavailable",
            "source_count": 1,
            "time_coverage": {"start": "2026-01-01", "end": "2026-03-01"},
            "evidence_completeness_rate": "unavailable",
            "unresolved_skill_ratio": 1.0,
        },
        "excluded_samples": [],
    }
    assert data["clusters"][0]["germination_assessment"]["evidence_package"]["algorithm_version"]
    queried = client.get(
        f"/api/v1/discovery-runs/{data['run_id']}", headers=HEADERS
    )
    assert queried.status_code == 200
    assert queried.json()["data"]["input_quality_report"] == data["input_quality_report"]


def test_public_contract_persists_explicit_current_observation_window():
    payload = _payload("explicit-observation-window")
    payload["current_observation_window_id"] = "2026-02"

    response = client.post("/api/v1/discovery-runs", json=payload, headers=HEADERS)

    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert (
        data["run_context"]["time_window"]["current_observation_window_id"]
        == "2026-02"
    )
    with SessionLocal() as db:
        run = db.get(DiscoveryRun, data["run_id"])
        assert run.time_window_start.isoformat() == "2026-02-01"
        assert run.time_window_end.isoformat() == "2026-02-28"


def test_repeated_runs_with_distinct_request_ids_are_distinct_and_reproducible():
    first = client.post("/api/v1/discovery-runs", json=_payload("first"), headers=HEADERS).json()["data"]
    second = client.post(
        "/api/v1/discovery-runs",
        json=_payload("second", month_offset=3),
        headers=HEADERS,
    ).json()["data"]
    assert first["run_id"] != second["run_id"]
    assert first["clusters"][0]["germination_assessment"]["germination_score"] == second["clusters"][0]["germination_assessment"]["germination_score"]
    with SessionLocal() as db:
        assert db.query(DiscoveryRun).count() == 2


def test_same_request_id_returns_the_original_run():
    first_payload = _payload("repeated-request")
    first = client.post(
        "/api/v1/discovery-runs", json=first_payload, headers=HEADERS
    )
    assert first.status_code == 201

    repeated_payload = _payload("repeated-request")
    repeated = client.post(
        "/api/v1/discovery-runs", json=repeated_payload, headers=HEADERS
    )
    assert repeated.status_code == 201
    assert repeated.json()["data"]["run_id"] == first.json()["data"]["run_id"]
    with SessionLocal() as db:
        assert db.query(DiscoveryRun).count() == 1


def test_failed_run_rolls_back_everything():
    payload = _payload()
    payload["snapshots"][0]["review_status"] = "draft"
    assert client.post("/api/v1/discovery-runs", json=payload, headers=HEADERS).status_code == 422
    with SessionLocal() as db:
        assert db.query(DiscoveryRun).count() == 0
        assert db.query(InputSnapshot).count() == 0


def _assert_422(payload: dict):
    response = client.post(
        "/api/v1/discovery-runs", json=payload, headers=HEADERS
    )
    assert response.status_code == 422
    assert response.json()["code"] == 422
    return response


def test_missing_unknown_and_legacy_contracts_are_rejected():
    missing = _payload("missing-contract")
    missing.pop("contract_version")
    unknown = _payload("unknown-contract")
    unknown["contract_version"] = "discovery.v999"
    legacy = {
        "request_id": "legacy",
        "snapshots": [
            {
                "jd_id": f"legacy-jd-{index}",
                "schema_version": "v2",
                "review_status": "approved",
                "title": "legacy",
                "publish_date": f"2026-0{index}-01",
                "structured_data": {
                    "responsibilities": ["legacy"],
                    "required_skills": [{"raw_skill": "Python"}],
                    "bonus_skills": [],
                    "business_scenarios": [],
                },
            }
            for index in (1, 2)
        ],
        "position_references": [
            {
                "position_id": "formal-java",
                "graph_version_id": "graph-v1",
                "required_skills": [{"raw_skill": "Java"}],
            }
        ],
    }
    for value in (missing, unknown, legacy):
        _assert_422(value)


def test_extra_and_misspelled_fields_are_rejected():
    top_level = _payload("extra-top-level")
    top_level["algoritm"] = "default"
    snapshot = _payload("extra-snapshot")
    snapshot["snapshots"][0]["source_fact_verison"] = "2"
    structured = _payload("extra-structured")
    structured["snapshots"][0]["structured_data"]["responsibilites"] = ["typo"]
    skill = _payload("extra-skill")
    skill["snapshots"][0]["structured_data"]["required_skills"][0]["confidence"] = 1
    for value in (top_level, snapshot, structured, skill):
        response = _assert_422(value)
        assert "extra_forbidden" in str(response.json()["data"])


def test_invalid_identity_status_path_and_missing_structure_are_rejected():
    empty_fact_id = _payload("empty-fact-id")
    empty_fact_id["snapshots"][0]["source_fact_id"] = " "
    empty_fact_version = _payload("empty-fact-version")
    empty_fact_version["snapshots"][0]["source_fact_version"] = ""
    invalid_status = _payload("invalid-status")
    invalid_status["snapshots"][0]["review_status"] = "draft"
    invalid_path = _payload("invalid-path")
    invalid_path["snapshots"][0]["review_status"] = "reviewed"
    invalid_path["snapshots"][0]["consumption_path"] = "published"
    missing_structured_field = _payload("missing-structured-field")
    missing_structured_field["snapshots"][0]["structured_data"].pop(
        "business_scenarios"
    )
    for value in (
        empty_fact_id,
        empty_fact_version,
        invalid_status,
        invalid_path,
        missing_structured_field,
    ):
        _assert_422(value)


def test_empty_evidence_and_approved_compatibility_remain_available():
    value = _payload("empty-evidence")
    value["snapshots"][0]["structured_data"]["responsibilities"] = []
    value["snapshots"][0]["structured_data"]["required_skills"] = []
    value["snapshots"][0]["review_status"] = "approved"
    value["snapshots"][0]["consumption_path"] = None
    response = client.post(
        "/api/v1/discovery-runs", json=value, headers=HEADERS
    )
    assert response.status_code == 201


def test_window_compatibility_and_unknown_algorithm_rejection():
    outside = _payload("outside-window")
    outside["snapshots"][0]["publish_date"] = "2025-12-31"
    outside_response = client.post(
        "/api/v1/discovery-runs", json=outside, headers=HEADERS
    )
    assert outside_response.status_code == 422

    out_of_order = _payload("out-of-order-windows")
    out_of_order["time_windows"] = list(reversed(out_of_order["time_windows"]))
    out_of_order_response = client.post(
        "/api/v1/discovery-runs", json=out_of_order, headers=HEADERS
    )
    assert out_of_order_response.status_code == 422
    assert "chronological order" in str(out_of_order_response.json()["data"])

    unknown_algorithm = _payload("unknown-algorithm")
    unknown_algorithm["algorithm"] = "typo-clustering"
    response = _assert_422(unknown_algorithm)
    assert "emerge_v3_2" in str(response.json()["data"])

    incomplete_window = _payload("incomplete-window")
    incomplete_window["time_windows"].pop()
    assert (
        client.post(
            "/api/v1/discovery-runs", json=incomplete_window, headers=HEADERS
        ).status_code
        == 422
    )

    no_window = _payload("no-window")
    no_window.pop("time_windows")
    assert (
        client.post(
            "/api/v1/discovery-runs", json=no_window, headers=HEADERS
        ).status_code
        == 422
    )

    invalid_publish_date = _payload("invalid-publish-date")
    invalid_publish_date["snapshots"][0]["publish_date"] = "not-a-date"
    _assert_422(invalid_publish_date)


def test_duplicate_jd_id_is_deduplicated_by_precheck_and_missing_quality_fields_are_rejected():
    from dataclasses import replace

    from app.application.input_quality import precheck_discovery_input
    from app.api.mapping import discovery_command_from_api

    command = discovery_command_from_api(
        contract_version="discovery.v2",
        request_id="precheck-duplicate",
        algorithm="emerge_v3_2",
        time_windows=_payload()["time_windows"],
        snapshots=_payload()["snapshots"],
        position_references=_payload()["position_references"],
        config=_payload()["config"],
    )
    duplicate_command = replace(
        command,
        snapshots=(
            command.snapshots[0],
            command.snapshots[0],
            command.snapshots[2],
        ),
    )
    report = precheck_discovery_input(
        duplicate_command.snapshots,
        time_window_start=duplicate_command.time_window_start,
        time_window_end=duplicate_command.time_window_end,
    ).report
    assert report["raw_jd_count"] == 3
    assert report["valid_jd_count"] == 2
    assert report["deduplicated_jd_count"] == 2
    assert report["duplicate_jd_count"] == 1
    assert report["excluded_samples"][0]["reasons"] == ("duplicate_jd_id",)

    incomplete = _payload("quality-unavailable")
    incomplete["snapshots"][0]["source_name"] = None
    incomplete["snapshots"][0]["publish_date"] = None
    result = client.post(
        "/api/v1/discovery-runs", json=incomplete, headers=HEADERS
    )
    assert result.status_code == 422


def test_cross_window_lineage_and_run_context_are_queryable():
    first_payload = _payload("lineage-window-1")
    first = client.post(
        "/api/v1/discovery-runs", json=first_payload, headers=HEADERS
    )
    assert first.status_code == 201
    assert {
        item["relation_type"] for item in first.json()["data"]["lineages"]
    } == {"birth"}

    second_payload = _payload("lineage-window-2")
    second_payload["time_windows"] = [
        {"window_id": "2026-04", "start": "2026-04-01", "end": "2026-04-30"},
        {"window_id": "2026-05", "start": "2026-05-01", "end": "2026-05-31"},
        {"window_id": "2026-06", "start": "2026-06-01", "end": "2026-06-30"},
    ]
    for index, snapshot in enumerate(second_payload["snapshots"], start=4):
        snapshot["jd_id"] = f"window-2-jd-{index}"
        snapshot["publish_date"] = f"2026-0{index}-01"
        snapshot["content_hash"] = "sha256:" + hashlib.sha256(
            snapshot["jd_id"].encode()
        ).hexdigest()
        snapshot["structured_data"]["source_record_id"] = (
            f"{snapshot['jd_id']}-source"
        )
    second = client.post(
        "/api/v1/discovery-runs", json=second_payload, headers=HEADERS
    )
    assert second.status_code == 201
    data = second.json()["data"]
    continuation = next(
        item for item in data["lineages"]
        if item["relation_type"] == "continue"
    )
    assert continuation["similarity_score"] >= continuation["evidence"]["threshold"]
    assert continuation["evidence"]["score_components"]["member_overlap"] == 1.0
    assert continuation["evidence"]["decision_reason"]
    assert continuation["evidence"]["score_components"]

    cluster = next(
        item for item in data["clusters"]
        if item["cluster_id"] == continuation["successor_cluster_id"]
    )
    explanation = cluster["explainability"]
    assert explanation["member_fact_identities"] == [
        {
            "source_fact_id": f"fact-{index}",
            "source_fact_version": "1",
        }
        for index in range(3)
    ]
    assert explanation["core_skills"]
    assert explanation["title_distribution"] == {"大模型应用开发工程师": 3}
    assert explanation["enterprise_distribution"] == "unavailable"
    assert explanation["source_distribution"] == {"platform-a": 3}
    assert cluster["lineage_relations"][0]["relation_type"] == "continue"

    context = data["run_context"]
    assert context["time_window"]["start"] == "2026-04-01"
    assert context["time_window"]["end"] == "2026-06-30"
    assert len(context["time_window"]["windows"]) == 3
    assert context["algorithm"]["algorithm_version"]
    assert context["config"]
    assert context["position_references"]
    assert context["position_graph_versions"] == {"formal-java": "graph-v1"}

    queried = client.get(
        f"/api/v1/discovery-runs/{data['run_id']}", headers=HEADERS
    )
    assert queried.status_code == 200
    assert queried.json()["data"]["run_context"] == context
    assert queried.json()["data"]["lineages"] == data["lineages"]

    incompatible = _payload("lineage-window-3-incompatible")
    incompatible["time_windows"] = [
        {"window_id": "2026-07", "start": "2026-07-01", "end": "2026-07-31"},
        {"window_id": "2026-08", "start": "2026-08-01", "end": "2026-08-31"},
        {"window_id": "2026-09", "start": "2026-09-01", "end": "2026-09-30"},
    ]
    incompatible["config"] = {
        "dataset_id": "emerging-discovery-full-temporal-v1",
        "emerging_threshold": 0.7,
    }
    for index, snapshot in enumerate(incompatible["snapshots"], start=7):
        snapshot["publish_date"] = f"2026-0{index}-01"
    incompatible_response = client.post(
        "/api/v1/discovery-runs", json=incompatible, headers=HEADERS
    )
    assert incompatible_response.status_code == 201
    assert {
        item["relation_type"]
        for item in incompatible_response.json()["data"]["lineages"]
    } == {"birth"}
