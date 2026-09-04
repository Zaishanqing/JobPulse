import pytest
from fastapi.testclient import TestClient

from tests.runtime_database import reset_database_data, Base, engine
from app.main import app


client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_database():
    reset_database_data()
    yield
    reset_database_data()


def _headers(username: str) -> dict:
    client.post(
        "/api/v1/auth/register",
        json={
            "role": "personal_user",
            "username": username,
            "password": "password123",
            "email": f"{username}@example.com",
            "phone": "13800000000",
        },
    )
    token = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "password123"},
    ).json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_disabled_ocr_is_persisted_as_failure_without_fake_text_and_can_be_edited():
    headers = _headers("ocr_owner")
    response = client.post(
        "/api/v1/ocr/image",
        headers=headers,
        files={"file": ("resume.png", b"not-an-image", "image/png")},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "failed"
    assert data["text"] is None
    assert data["provider"] == "disabled"
    assert data["error_code"] == "IntegrationUnavailableError"
    assert "占位文本" not in str(data)

    task = client.get(f"/api/v1/ocr/tasks/{data['task_id']}", headers=headers)
    assert task.status_code == 200
    assert task.json()["data"]["canonical_status"] == "failed"

    edited = client.put(
        f"/api/v1/ocr/results/{data['result_id']}",
        headers=headers,
        json={"text": "人工校正后的真实文本"},
    )
    assert edited.status_code == 200
    edited_data = edited.json()["data"]
    assert edited_data["status"] == "manually_edited"
    assert edited_data["text"] == "人工校正后的真实文本"
    assert edited_data["error_code"] is None


def test_ocr_result_edit_enforces_ownership_and_text_input():
    owner_headers = _headers("ocr_owner_two")
    other_headers = _headers("ocr_other_two")
    result_id = client.post(
        "/api/v1/ocr/pdf",
        headers=owner_headers,
        files={"file": ("resume.pdf", b"%PDF", "application/pdf")},
    ).json()["data"]["result_id"]

    forbidden = client.put(
        f"/api/v1/ocr/results/{result_id}",
        headers=other_headers,
        json={"text": "unauthorized"},
    )
    missing_text = client.put(
        f"/api/v1/ocr/results/{result_id}",
        headers=owner_headers,
        json={},
    )

    assert forbidden.status_code == 403
    assert missing_text.status_code == 422
