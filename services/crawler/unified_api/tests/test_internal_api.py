from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from unified_api.auth import require_internal_token
from unified_api.routers import internal_router


@pytest.fixture()
def client(monkeypatch):
    application = FastAPI()
    application.include_router(internal_router.router)
    application.dependency_overrides[require_internal_token] = lambda: None
    # Isolate the internal API from crawler MySQL and real browser services.
    monkeypatch.setattr(
        internal_router.boss_service,
        "boss_login_status",
        lambda: {
            "logged_in": True,
            "cookie_count": 3,
            "running": False,
            "status": "succeeded",
            "login_id": "login-1",
            "started_at": "2026-08-17T00:00:00Z",
            "finished_at": "2026-08-17T00:01:00Z",
            "message": None,
            "updated_at": "2026-08-17T00:01:00Z",
        },
    )
    monkeypatch.setattr(
        internal_router.liepin_service,
        "liepin_login_status",
        lambda: {
            "logged_in": False,
            "cookie_count": 0,
            "running": False,
            "status": "idle",
            "login_id": None,
            "started_at": None,
            "finished_at": None,
            "message": None,
            "updated_at": None,
        },
    )
    calls: dict[str, object] = {}

    def fake_start_task(user_id, task_type, params, run_func):
        calls["start"] = {"user_id": user_id, "task_type": task_type, "params": params}
        return "task-1"

    monkeypatch.setattr(internal_router, "start_task", fake_start_task)

    def fake_get_task_status(task_id):
        calls["get"] = task_id
        return {
            "task_id": task_id,
            "task_type": "boss",
            "status": "completed",
            "progress": "done",
            "result_count": 3,
            "error_message": None,
        }

    monkeypatch.setattr(internal_router, "get_task_status", fake_get_task_status)

    class FakeExporter:
        def __init__(self, repository):
            self.repository = repository

        def export(self, *, output: Path, mode, task_id=None):
            calls["export_output"] = output
            calls["export_mode"] = mode
            calls["export_task_id"] = task_id
            return SimpleNamespace(
                bundle_id="bundle-1",
                output_path=output / "nfbs-jd-bundle-v1-test.zip",
                record_count=3,
            )

    monkeypatch.setattr(internal_router, "BundleExporter", FakeExporter)
    monkeypatch.setattr(internal_router, "MySQLExportRepository", lambda: object())
    return TestClient(application), calls


def test_sources_contract(client):
    test_client, _ = client
    response = test_client.get("/internal/v1/sources")
    assert response.status_code == 200
    data = response.json()["data"]["sources"]
    assert {item["source"] for item in data} == {"boss", "liepin", "feishu"}
    boss = next(item for item in data if item["source"] == "boss")
    assert boss["ready"] is True
    assert boss["login_required"] is False
    liepin = next(item for item in data if item["source"] == "liepin")
    assert liepin["ready"] is False
    assert liepin["login_required"] is True
    feishu = next(item for item in data if item["source"] == "feishu")
    assert feishu["ready"] is True
    assert feishu["login_required"] is False


def test_liepin_login_status_contract(client):
    test_client, _ = client
    response = test_client.get("/internal/v1/liepin/login/status")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["logged_in"] is False
    assert data["status"] == "idle"


def test_boss_login_status_contract(client):
    test_client, _ = client
    response = test_client.get("/internal/v1/boss/login/status")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["logged_in"] is True
    assert data["cookie_count"] == 3
    assert data["status"] == "succeeded"


def test_crawl_contract(client):
    test_client, calls = client
    response = test_client.post(
        "/internal/v1/crawl",
        json={"source": "boss", "keyword": "Java", "city": "北京", "pages": 3},
    )
    assert response.status_code == 200
    assert response.json()["data"]["task_id"] == "task-1"
    start = calls["start"]
    assert start["task_type"] == "boss"
    assert start["params"] == {"keyword": "Java", "city": "北京", "pages": 3}


def test_feishu_crawl_contract(client):
    test_client, calls = client
    response = test_client.post(
        "/internal/v1/crawl",
        json={"source": "feishu", "pages": 3},
    )
    assert response.status_code == 200
    assert response.json()["data"]["task_id"] == "task-1"
    start = calls["start"]
    assert start["task_type"] == "feishu"
    assert start["params"] == {"company_name": "all", "platform": "feishu"}


def test_task_contract(client):
    test_client, calls = client
    response = test_client.get("/internal/v1/tasks/task-1")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["task_id"] == "task-1"
    assert data["status"] == "completed"
    assert data["result_count"] == 3
    assert calls["get"] == "task-1"


def test_export_contract(client, tmp_path, monkeypatch):
    test_client, calls = client
    monkeypatch.setenv("OFFLINE_BUNDLE_DIR", str(tmp_path))
    response = test_client.post(
        "/internal/v1/export",
        json={"task_id": "task-1", "source": "boss"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["bundle_id"] == "bundle-1"
    assert data["file_name"] == "nfbs-jd-bundle-v1-test.zip"
    assert data["record_count"] == 3
    assert calls["export_output"] == tmp_path
    assert calls["export_task_id"] == "task-1"


def test_export_rejects_task_source_mismatch(client):
    test_client, _ = client
    response = test_client.post(
        "/internal/v1/export",
        json={"task_id": "task-1", "source": "liepin"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "task_source_mismatch"


def test_internal_auth_rejects_missing_token():
    # Build a router with the real dependency to prove the Bearer gate.
    application = FastAPI()
    application.include_router(internal_router.router)
    test_client = TestClient(application)
    response = test_client.get("/internal/v1/sources")
    assert response.status_code == 401
