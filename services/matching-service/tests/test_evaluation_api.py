from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
client.headers.update({"Authorization": "Bearer test-token"})


def test_evaluation_api_runs_independently(ready_cv_json, ready_position_json):
    response = client.post(
        "/api/v1/evaluations",
        json={"cv_profile": ready_cv_json, "position_profile": ready_position_json},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["evaluation_status"] == "completed"
    assert body["data"]["algorithm_version"] == "deterministic-matching.v9"
    for field in (
        "responsibility_results",
        "project_results",
        "scenario_results",
        "responsibility_coverage",
        "project_coverage",
        "scenario_coverage",
        "required_transferable_coverage",
        "bonus_transferable_coverage",
    ):
        assert field in body["data"]
    required_skill = next(
        item
        for item in body["data"]["skill_results"]
        if item["importance_level"] == "required"
    )
    assert required_skill["match_type"] == "exact"
    assert required_skill["related_candidate_skill_id"] is None
    assert required_skill["relation_type"] is None
    assert required_skill["relation_evidence"] == []


def test_evaluation_api_maps_business_error_without_fastapi_exception():
    response = client.post("/api/v1/evaluations", json={})

    assert response.status_code == 200
    assert response.json()["data"]["evaluation_status"] == "rejected"
    assert response.json()["data"]["error_code"] == "CV_PROFILE_NOT_FOUND"

    response = client.post("/api/v1/evaluations")
    assert response.status_code == 200
    assert response.json()["data"]["error_code"] == "EVALUATION_REQUEST_INVALID"
