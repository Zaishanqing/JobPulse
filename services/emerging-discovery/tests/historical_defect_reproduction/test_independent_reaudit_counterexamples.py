"""Independent counterexamples for the 2026-07-15 emerging-discovery re-audit.

These tests document reproducible current behaviour.  A passing assertion here can
therefore prove that a required safety property is absent; it is not an acceptance
suite for the intended architecture.
"""

from __future__ import annotations

import inspect
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import event, text
from fastapi.testclient import TestClient

from app.application.discovery import RunDiscovery
from app.bootstrap.application import create_app
from tests.runtime_database import SessionLocal
from app.infrastructure.models import (
    AlgorithmConfigSnapshot,
    Cluster,
    ClusterMembership,
    DiscoveryRun,
    GerminationAssessment,
    InputSnapshot,
)
from app.infrastructure.providers import (
    DeterministicEmbeddingProvider,
    PayloadPositionReferenceProvider,
    RuleBasedClusteringAlgorithm,
    RuleBasedDefinitionGenerator,
)


@pytest.fixture
def client():
    with TestClient(create_app(), raise_server_exceptions=True) as value:
        yield value


def _payload(request_id: str = "audit") -> dict:
    return {
        "request_id": request_id,
        "snapshots": [
            {
                "jd_id": f"jd-{index}",
                "schema_version": "v2",
                "review_status": "approved",
                "title": "大模型应用开发工程师",
                "source_name": "only-platform",
                "publish_date": f"2026-0{index + 1}-01",
                "structured_data": {
                    "required_skills": [{"raw_skill": "RAG"}, {"raw_skill": "Python"}],
                    "bonus_skills": [],
                    "industry": "人工智能",
                    "business_scenarios": ["智能客服"],
                },
            }
            for index in range(3)
        ],
        "config": {},
    }


def _counts(db) -> tuple[int, ...]:
    return tuple(
        db.query(model).count()
        for model in (
            DiscoveryRun,
            InputSnapshot,
            AlgorithmConfigSnapshot,
            Cluster,
            ClusterMembership,
            GerminationAssessment,
        )
    )


def test_contract_rejects_unapproved_but_accepts_non_v2_and_duplicate_jd(client):
    draft = _payload()
    draft["snapshots"][0]["review_status"] = "draft"
    assert client.post("/api/v1/discovery-runs", json=draft).status_code == 422

    non_v2 = _payload()
    non_v2["snapshots"][0]["schema_version"] = "v1"
    assert client.post("/api/v1/discovery-runs", json=non_v2).status_code == 201

    duplicate = _payload()
    duplicate["snapshots"][1]["jd_id"] = duplicate["snapshots"][0]["jd_id"]
    # The duplicate reaches persistence and becomes a server error instead of a
    # stable 4xx validation error.
    with pytest.raises(Exception):
        client.post("/api/v1/discovery-runs", json=duplicate)


def test_input_order_changes_fingerprint(client):
    first_payload = _payload("ordered")
    second_payload = _payload("reversed")
    second_payload["snapshots"].reverse()
    first = client.post("/api/v1/discovery-runs", json=first_payload).json()["data"]
    second = client.post("/api/v1/discovery-runs", json=second_payload).json()["data"]
    assert first["input_fingerprint"] != second["input_fingerprint"]


def test_json_object_key_order_does_not_change_fingerprint(client):
    first_payload = _payload("json-a")
    second_payload = _payload("json-b")
    structured = second_payload["snapshots"][0]["structured_data"]
    second_payload["snapshots"][0]["structured_data"] = dict(reversed(list(structured.items())))
    first = client.post("/api/v1/discovery-runs", json=first_payload).json()["data"]
    second = client.post("/api/v1/discovery-runs", json=second_payload).json()["data"]
    assert first["input_fingerprint"] == second["input_fingerprint"]


def test_same_fingerprint_concurrent_requests_create_duplicate_runs(client):
    def submit(index: int):
        return client.post("/api/v1/discovery-runs", json=_payload(f"concurrent-{index}"))

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(submit, range(2)))
    assert [response.status_code for response in responses] == [201, 201]
    fingerprints = [response.json()["data"]["input_fingerprint"] for response in responses]
    assert fingerprints[0] == fingerprints[1]
    assert responses[0].json()["data"]["run_id"] != responses[1].json()["data"]["run_id"]


@pytest.mark.parametrize("fail_on_flush", [1, 2, 3, 4])
def test_transaction_rolls_back_when_each_flush_stage_fails(fail_on_flush):
    from app.api.contracts import DiscoveryRunRequest

    use_case = RunDiscovery(
        DeterministicEmbeddingProvider(),
        RuleBasedClusteringAlgorithm(),
        PayloadPositionReferenceProvider(),
        RuleBasedDefinitionGenerator(),
    )
    with SessionLocal() as db:
        counter = {"value": 0}

        def fail(_session, _flush_context, _instances):
            counter["value"] += 1
            if counter["value"] == fail_on_flush:
                raise RuntimeError(f"injected flush failure {fail_on_flush}")

        event.listen(db, "before_flush", fail)
        with pytest.raises(RuntimeError):
            use_case.execute(db, DiscoveryRunRequest.model_validate(_payload()))
        event.remove(db, "before_flush", fail)
        assert _counts(db) == (0, 0, 0, 0, 0, 0)


def test_orm_bulk_update_bypasses_mapper_immutability_without_migration_triggers(client):
    run_id = client.post("/api/v1/discovery-runs", json=_payload()).json()["data"]["run_id"]
    with SessionLocal() as db:
        changed = db.query(DiscoveryRun).filter_by(id=run_id).update({"status": "overwritten"})
        db.commit()
        assert changed == 1
        assert db.get(DiscoveryRun, run_id).status == "overwritten"


def test_native_sql_delete_bypasses_mapper_immutability_without_migration_triggers(client):
    run_id = client.post("/api/v1/discovery-runs", json=_payload()).json()["data"]["run_id"]
    with SessionLocal() as db:
        db.execute(text("DELETE FROM germination_assessments"))
        db.execute(text("DELETE FROM cluster_memberships"))
        db.execute(text("DELETE FROM clusters"))
        db.execute(text("DELETE FROM algorithm_config_snapshots"))
        db.execute(text("DELETE FROM input_snapshots"))
        db.execute(text("DELETE FROM discovery_runs WHERE id = :run_id"), {"run_id": run_id})
        db.commit()
        assert db.get(DiscoveryRun, run_id) is None


def test_production_bootstrap_has_no_auth_gate_and_wires_payload_fake_reference_provider(client):
    source = inspect.getsource(create_app)
    assert "PayloadPositionReferenceProvider()" in source
    assert "Depends(" not in source
    assert client.post("/api/v1/discovery-runs", json=_payload()).status_code == 201


def test_rule_clustering_ignores_embedding_values_and_drops_unknown_jobs():
    algorithm = RuleBasedClusteringAlgorithm()
    snapshots = _payload()["snapshots"]
    assert algorithm.cluster(snapshots, [[0.0], [0.0], [0.0]]) == algorithm.cluster(
        snapshots, [[999.0], [-10.0], [3.14]]
    )
    unknown = [
        {
            "jd_id": "unknown",
            "title": "会计",
            "structured_data": {"required_skills": [{"raw_skill": "Excel"}]},
        }
    ]
    assert algorithm.cluster(unknown, [[0.5]]) == []


def test_small_sample_can_receive_high_raw_score_but_is_gated(client):
    payload = _payload()
    payload["snapshots"] = payload["snapshots"][:1]
    payload["config"] = {
        "growth": 0,
        "novelty": 0,
        "diversity": 0,
        "industry_spread": 0,
        "distance": 1,
        "sample_size_penalty": 0,
        "emerging_threshold": 0.7,
    }
    result = client.post("/api/v1/discovery-runs", json=payload).json()["data"]["clusters"][0]
    assessment = result["germination_assessment"]
    assert assessment["germination_score"] == pytest.approx(0.78)
    assert assessment["qualified_as_emerging"] is False


def test_config_snapshot_preserves_old_run_explanation(client):
    first_payload = _payload("config-old")
    first_payload["config"]["single_platform_noise_penalty"] = 0.01
    second_payload = _payload("config-new")
    second_payload["config"]["single_platform_noise_penalty"] = 0.40
    first = client.post("/api/v1/discovery-runs", json=first_payload).json()["data"]
    second = client.post("/api/v1/discovery-runs", json=second_payload).json()["data"]
    old_again = client.get(f"/api/v1/discovery-runs/{first['run_id']}").json()["data"]
    old_penalty = first["clusters"][0]["germination_assessment"]["score_dimensions"]["single_platform_noise_penalty"]
    new_penalty = second["clusters"][0]["germination_assessment"]["score_dimensions"]["single_platform_noise_penalty"]
    assert old_penalty == old_again["clusters"][0]["germination_assessment"]["score_dimensions"]["single_platform_noise_penalty"]
    assert old_penalty != new_penalty
