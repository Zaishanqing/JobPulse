from pathlib import Path
import shutil
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from tests.runtime_database import reset_database_data, Base, engine
from app.main import app
from app.integrations.registry import reset_integration_registry


client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_database(monkeypatch):
    upload_dir = Path("data") / f"test_uploads_{uuid4().hex}"
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(upload_dir))
    reset_integration_registry()
    reset_database_data()
    yield
    reset_database_data()
    shutil.rmtree(upload_dir, ignore_errors=True)
    reset_integration_registry()


def _register_and_login(username: str) -> dict[str, str]:
    client.post(
        "/api/v1/auth/register",
        json={
            "role": "personal_user",
            "username": username,
            "password": "password123",
        },
    )
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "password123"},
    )
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def test_upload_sanitizes_filename_and_hides_server_path():
    headers = _register_and_login("file_owner001")
    response = client.post(
        "/api/v1/files/upload",
        files={"file": ("../private.txt", b"safe test content", "text/plain")},
        headers=headers,
    )

    data = response.json()["data"]
    assert response.status_code == 200
    assert data["filename"] == "private.txt"
    assert data["path"] == data["storage_key"]
    assert Path(data["path"]).name == data["path"]
    assert "uploads" not in data["path"]


def test_file_owner_is_enforced_for_detail_and_delete():
    owner_headers = _register_and_login("file_owner002")
    other_headers = _register_and_login("file_other002")
    uploaded = client.post(
        "/api/v1/files/upload",
        files={"file": ("resume.txt", b"resume", "text/plain")},
        headers=owner_headers,
    )
    file_id = uploaded.json()["data"]["file_id"]

    assert client.get(
        f"/api/v1/files/{file_id}", headers=other_headers
    ).status_code == 403
    assert client.delete(
        f"/api/v1/files/{file_id}", headers=other_headers
    ).status_code == 403
    assert client.get(
        f"/api/v1/files/{file_id}", headers=owner_headers
    ).status_code == 200
    preview = client.get(f"/api/v1/files/{file_id}/preview", headers=owner_headers)
    assert preview.status_code == 200
    assert preview.content == b"resume"
    assert preview.headers["content-type"].startswith("text/plain")
    assert preview.headers["content-disposition"] == 'inline; filename="resume.txt"'
    assert client.get(f"/api/v1/files/{file_id}/preview", headers=other_headers).status_code == 403


def test_preview_supports_non_ascii_filename():
    headers = _register_and_login("file_owner004")
    uploaded = client.post(
        "/api/v1/files/upload",
        files={"file": ("张三简历.txt", b"resume", "text/plain")},
        headers=headers,
    )
    file_id = uploaded.json()["data"]["file_id"]

    preview = client.get(f"/api/v1/files/{file_id}/preview", headers=headers)
    assert preview.status_code == 200
    assert preview.content == b"resume"
    disposition = preview.headers["content-disposition"]
    assert disposition.startswith("inline; ")
    assert "filename*=UTF-8''" in disposition
    disposition.encode("latin-1")


def test_upload_rejects_unsupported_empty_and_oversized_files(monkeypatch):
    headers = _register_and_login("file_owner003")
    unsupported = client.post(
        "/api/v1/files/upload",
        files={"file": ("payload.exe", b"binary", "application/octet-stream")},
        headers=headers,
    )
    empty = client.post(
        "/api/v1/files/upload",
        files={"file": ("empty.txt", b"", "text/plain")},
        headers=headers,
    )
    monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE_BYTES", 4)
    oversized = client.post(
        "/api/v1/files/upload",
        files={"file": ("large.txt", b"12345", "text/plain")},
        headers=headers,
    )

    assert unsupported.status_code == 415
    assert empty.status_code == 400
    assert oversized.status_code == 413
