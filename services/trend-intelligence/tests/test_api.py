from copy import deepcopy


def test_health_is_public_and_internal_routes_require_bearer(client, payload):
    assert client.get("/health/live").status_code == 200
    assert client.get("/readiness").status_code == 200
    response = client.post("/internal/v1/analysis-runs", json=payload)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_validation_rejects_invalid_contract_window_sources_and_weights(client, auth, payload):
    invalid = deepcopy(payload)
    invalid["contract_version"] = "trend-analysis.v2"
    invalid["time_window"]["end"] = invalid["time_window"]["start"]
    invalid["data_sources"] = []
    invalid["weights"] = {"recency": -1}
    assert client.post("/internal/v1/analysis-runs", headers=auth, json=invalid).status_code == 422


def test_create_get_logs_and_cancel(client, auth, payload):
    created = client.post("/internal/v1/analysis-runs", headers=auth, json=payload)
    assert created.status_code == 202
    run = created.json()["data"]
    assert run["status"] == "pending"

    fetched = client.get(f"/internal/v1/analysis-runs/{run['id']}", headers=auth)
    assert fetched.json()["data"]["id"] == run["id"]
    logs = client.get(f"/internal/v1/analysis-runs/{run['id']}/logs", headers=auth)
    assert [item["event"] for item in logs.json()["data"]] == ["created"]
    cancelled = client.post(f"/internal/v1/analysis-runs/{run['id']}/cancel", headers=auth)
    assert cancelled.json()["data"]["status"] == "cancelled"


def test_request_id_and_idempotency_key_are_independently_idempotent(client, auth, payload):
    first = client.post("/internal/v1/analysis-runs", headers=auth, json=payload).json()["data"]
    changed = deepcopy(payload)
    changed["idempotency_key"] = "other-key"
    by_request = client.post("/internal/v1/analysis-runs", headers=auth, json=changed)
    assert by_request.json()["data"]["id"] == first["id"]

    changed["request_id"] = "other-request"
    changed["idempotency_key"] = payload["idempotency_key"]
    by_key = client.post("/internal/v1/analysis-runs", headers=auth, json=changed)
    assert by_key.json()["data"]["id"] == first["id"]


def test_same_request_id_with_different_idempotency_key_reuses_run(client, auth, payload):
    first = client.post("/internal/v1/analysis-runs", headers=auth, json=payload).json()["data"]
    conflicting = deepcopy(payload)
    conflicting["idempotency_key"] = "other-key"
    conflicting["weights"] = {"recency": 1.0}
    response = client.post("/internal/v1/analysis-runs", headers=auth, json=conflicting)
    assert response.status_code == 202
    assert response.json()["data"]["id"] == first["id"]


def test_same_idempotency_key_with_different_request_id_reuses_run(client, auth, payload):
    first = client.post("/internal/v1/analysis-runs", headers=auth, json=payload).json()["data"]
    conflicting = deepcopy(payload)
    conflicting["request_id"] = "other-request"
    conflicting["algorithm_version"] = "market-emergence.v2"
    response = client.post("/internal/v1/analysis-runs", headers=auth, json=conflicting)
    assert response.status_code == 202
    assert response.json()["data"]["id"] == first["id"]


def test_reordered_sources_and_weights_are_idempotent_under_same_identity(client, auth, payload):
    first = client.post("/internal/v1/analysis-runs", headers=auth, json=payload).json()["data"]
    reordered = deepcopy(payload)
    reordered["data_sources"] = list(reversed(reordered["data_sources"]))
    reordered["weights"] = {"authority": 0.4, "recency": 0.6}
    second = client.post("/internal/v1/analysis-runs", headers=auth, json=reordered).json()["data"]
    assert second["id"] == first["id"]


def test_position_skill_trend_contract_requires_main_skill_ids_and_is_idempotent(client, auth, payload):
    skill_payload = deepcopy(payload)
    skill_payload.update({
        "run_type": "position_skill_trend",
        "position_id": "position-1",
        "position_name": "Java engineer",
        "graph_version": "graph-1",
        "standard_skills": [
            {"skill_id": "skill-spring", "skill_name": "Spring Boot", "aliases": ["SpringBoot", "Spring"]},
            {"skill_id": "skill-java", "skill_name": "Java", "aliases": ["JVM language"]},
        ],
        "skill_catalog_version": "catalog-v1",
        "config_version": "config-v1",
    })
    first = client.post(
        "/internal/v1/analysis-runs", headers=auth, json=skill_payload
    )
    assert first.status_code == 202
    run = first.json()["data"]
    assert run["run_type"] == "position_skill_trend"
    assert client.get(
        f"/internal/v1/analysis-runs/{run['id']}/skill-trends", headers=auth
    ).status_code == 409

    reordered = deepcopy(skill_payload)
    reordered["request_id"] = skill_payload["request_id"]
    reordered["idempotency_key"] = skill_payload["idempotency_key"]
    reordered["standard_skills"] = list(reversed(reordered["standard_skills"]))
    reordered["standard_skills"][1]["aliases"].reverse()
    second = client.post(
        "/internal/v1/analysis-runs", headers=auth, json=reordered
    ).json()["data"]
    assert second["id"] == run["id"]

    invalid = deepcopy(skill_payload)
    invalid["request_id"] = "request-skill-invalid"
    invalid["idempotency_key"] = "idem-skill-invalid"
    invalid["standard_skills"] = [{"skill_name": "invented", "aliases": []}]
    assert client.post(
        "/internal/v1/analysis-runs", headers=auth, json=invalid
    ).status_code == 422
