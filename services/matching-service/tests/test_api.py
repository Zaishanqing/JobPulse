from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
client.headers.update({"Authorization": "Bearer test-token"})


def test_health_and_service_can_start_independently():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "message": "success",
        "data": {
            "status": "ok",
            "service": "matching-service",
            "version": "0.14.0",
        },
    }


def test_cv_validation_endpoint(cv_payload):
    response = client.post("/api/v1/profiles/cv/validate", json=cv_payload)

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["profile_status"] == "ready"
    assert body["data"]["profile_version"] == "cv-source.v1"
    assert body["data"]["validation_errors"] == []


def test_position_validation_endpoint(position_payload):
    response = client.post("/api/v1/profiles/position/validate", json=position_payload)

    assert response.status_code == 200
    assert response.json()["data"]["profile_status"] == "ready"


def test_invalid_schema_uses_validation_result_contract(cv_payload):
    del cv_payload["taxonomy_version"]

    response = client.post("/api/v1/profiles/cv/validate", json=cv_payload)

    assert response.status_code == 200
    assert response.json()["data"]["profile_status"] == "invalid"
    assert response.json()["data"]["validation_errors"][0]["error_type"] == "missing"
