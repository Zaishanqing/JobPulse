from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.runtime_database import reset_database_data


client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_database():
    reset_database_data()
    yield
    reset_database_data()


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register_and_login(username: str) -> str:
    client.post(
        "/api/v1/auth/register",
        json={
            "role": "personal_user",
            "username": username,
            "password": "password123",
            "email": f"{username}@example.com",
        },
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "password123"},
    )
    return login.json()["data"]["access_token"]


def test_request_validation_error_returns_sanitized_field_level_array():
    response = client.post(
        "/api/v1/auth/register",
        json={"role": "personal_user", "password": "short"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == 422
    assert body["message"] == "Validation error"
    assert isinstance(body["data"], list)
    assert len(body["data"]) >= 2

    fields = {item["field"] for item in body["data"]}
    assert "body.username" in fields
    assert "body.password" in fields
    for item in body["data"]:
        assert {"field", "error_type", "message"} <= set(item)
        assert "input" not in item
        assert "ctx" not in item


def test_request_validation_error_nested_array_location_is_stable():
    token = _register_and_login("validation_nested_user")
    response = client.post(
        "/api/v1/jds/batch",
        json=[{"title": "", "raw_text": ""}],
        headers=_headers(token),
    )

    assert response.status_code == 422
    body = response.json()
    assert isinstance(body["data"], list)
    fields = [item["field"] for item in body["data"]]
    assert "body[0].title" in fields
    assert "body[0].raw_text" in fields
    for item in body["data"]:
        assert {"field", "error_type", "message"} <= set(item)
        assert "input" not in item
