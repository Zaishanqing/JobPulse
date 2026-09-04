from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies.acquisition import get_acquisition_use_cases
from app.contexts.acquisition.application import AcquisitionUseCases
from app.contexts.acquisition.domain import AcquisitionJobRecord
from app.contexts.acquisition.ports import (
    BundleRef,
    CrawlerSourceStatus,
    CrawlerTaskRef,
    CrawlerTaskStatus,
)
from app.main import app
from app.offline_import.contracts import ImportSummary
from tests.offline_bundle_test_support import envelope, make_bundle
from tests.runtime_database import reset_database_data
from tests.user_factory import create_internal_user


@pytest.fixture(autouse=True)
def reset_database():
    reset_database_data()
    yield
    reset_database_data()


class FakeRepo:
    def __init__(self) -> None:
        self._records: dict[str, AcquisitionJobRecord] = {}
        self._claim_lock = threading.Lock()

    def add(self, record: AcquisitionJobRecord) -> None:
        self._records[record.id] = record

    def get(self, job_id: str) -> AcquisitionJobRecord | None:
        return self._records.get(job_id)

    def claim_pending(self, job_id: str, now: datetime) -> AcquisitionJobRecord | None:
        with self._claim_lock:
            record = self._records.get(job_id)
            if record is None or record.status != "pending":
                return None
            claimed = record.with_fields(
                status="crawling",
                started_at=now,
                progress=0.1,
                updated_at=now,
            )
            self._records[job_id] = claimed
            return claimed

    def list(self, *, status=None, source=None, offset=0, limit=20):
        values = [item for item in self._records.values()]
        if status is not None:
            values = [item for item in values if item.status == status]
        if source is not None:
            values = [item for item in values if item.source == source]
        values.sort(key=lambda item: str(item.created_at or ""), reverse=True)
        return values[offset : offset + limit], len(values)

    def save(self, record: AcquisitionJobRecord) -> None:
        self._records[record.id] = record

    def recover_stale(self, now, stale_after_seconds):
        return 0


class FakeGateway:
    def __init__(self) -> None:
        self.task_status = CrawlerTaskStatus("task-1", "completed", result_count=2)

    def list_sources(self):
        return [
            CrawlerSourceStatus("boss", True, True, False, None),
            CrawlerSourceStatus("liepin", True, False, True, "login required"),
            CrawlerSourceStatus("feishu", True, True, False, None),
        ]

    def save_boss_cookies(self, cookies):
        return {"saved": True, "count": len(cookies), "verified": True}

    def save_liepin_cookies(self, cookies):
        return {"saved": True, "count": len(cookies), "verified": True}

    def start_crawl(self, *, source, keyword, city, pages):
        return CrawlerTaskRef("task-1")

    def get_task(self, task_id):
        return self.task_status

    def export_bundle(self, *, task_id, source):
        return BundleRef("bundle-1", "bundle.zip", 2, "hash")


class FakeStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def resolve(self, bundle):
        return self._path


class FakeImporter:
    def import_bundle(self, path, *, allow_gap=False, retry=False):
        return ImportSummary(
            batch_id="batch-1",
            bundle_id="bundle-1",
            record_count=2,
            imported_count=1,
            skipped_count=1,
            failed_count=0,
            status="completed",
        )


class FakeUnitOfWork:
    def __init__(self, repo: FakeRepo) -> None:
        self.acquisition = repo

    def __enter__(self) -> "FakeUnitOfWork":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is not None:
            self.rollback()

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


class ImmediateRunner:
    def submit(self, fn) -> None:
        fn()


@pytest.fixture()
def acquisition_override(tmp_path):
    bundle_path = make_bundle(
        tmp_path / "bundle.zip",
        bundle_id="bundle-1",
        envelopes=[envelope("one", "text"), envelope("two", "text")],
    )
    repo = FakeRepo()
    use_cases = AcquisitionUseCases(
        lambda: FakeUnitOfWork(repo),
        FakeGateway(),
        FakeStore(bundle_path),
        FakeImporter(),
        ImmediateRunner(),
        poll_interval_seconds=0.01,
        timeout_seconds=1,
        clock=lambda: datetime(2026, 8, 17, tzinfo=timezone.utc),
        sleeper=lambda _: None,
    )
    app.dependency_overrides[get_acquisition_use_cases] = lambda: use_cases
    yield use_cases
    app.dependency_overrides.pop(get_acquisition_use_cases, None)


def _token(username: str, role: str) -> str:
    create_internal_user(username, role)
    response = TestClient(app).post(
        "/api/v1/auth/login",
        json={"username": username, "password": "password123"},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_create_returns_202_and_list_detail_work(acquisition_override):
    client = TestClient(app)
    token = _token("acq-admin", "admin")
    headers = _headers(token)
    create = client.post(
        "/api/v1/acquisition/jobs",
        headers=headers,
        json={"source": "boss", "keyword": "Java", "city": "北京", "pages": 2},
    )
    assert create.status_code == 202, create.text
    job = create.json()["data"]
    assert job["id"]
    assert job["status"] == "pending"

    list_response = client.get("/api/v1/acquisition/jobs", headers=headers)
    assert list_response.status_code == 200
    assert list_response.json()["data"]["total"] == 1

    detail = client.get(
        f"/api/v1/acquisition/jobs/{job['id']}", headers=headers
    )
    assert detail.status_code == 200
    assert detail.json()["data"]["id"] == job["id"]

    sources = client.get("/api/v1/acquisition/sources", headers=headers)
    assert sources.status_code == 200
    assert {item["source"] for item in sources.json()["data"]} == {"boss", "liepin", "feishu"}


def test_rbac_blocks_personal_user_from_create(acquisition_override):
    client = TestClient(app)
    token = _token("acq-personal", "personal_user")
    headers = _headers(token)
    response = client.post(
        "/api/v1/acquisition/jobs",
        headers=headers,
        json={"source": "boss", "keyword": "Java", "city": "北京", "pages": 2},
    )
    assert response.status_code == 403


def test_cookie_routes_are_registered(acquisition_override):
    client = TestClient(app)
    token = _token("acq-admin", "admin")
    headers = _headers(token)
    boss_cookies = client.post(
        "/api/v1/acquisition/boss/cookies",
        headers=headers,
        json={"cookies": [{"name": "wt2", "value": "x"}]},
    )
    assert boss_cookies.status_code == 202
    assert boss_cookies.json()["data"]["verified"] is True

    liepin_cookies = client.post(
        "/api/v1/acquisition/liepin/cookies",
        headers=headers,
        json={"cookies": [{"name": "lg"}]},
    )
    assert liepin_cookies.status_code == 202
    assert liepin_cookies.json()["data"]["count"] == 1


def test_feishu_create_does_not_require_keyword_or_city(acquisition_override):
    client = TestClient(app)
    token = _token("acq-admin", "admin")
    headers = _headers(token)
    create = client.post(
        "/api/v1/acquisition/jobs",
        headers=headers,
        json={"source": "feishu", "pages": 1},
    )
    assert create.status_code == 202, create.text
    data = create.json()["data"]
    assert data["source"] == "feishu"
    assert data["keyword"] == "all"
    assert data["city"] == "全国"


def test_retry_returns_new_job(acquisition_override):
    client = TestClient(app)
    token = _token("acq-admin", "admin")
    headers = _headers(token)
    # Make the first attempt fail so retry is allowed.
    acquisition_override._crawler_gateway.task_status = CrawlerTaskStatus(
        "task-1", "failed", error_message="boom"
    )
    created = client.post(
        "/api/v1/acquisition/jobs",
        headers=headers,
        json={"source": "boss", "keyword": "Java", "city": "北京", "pages": 1},
    ).json()["data"]
    # Let the retry succeed.
    acquisition_override._crawler_gateway.task_status = CrawlerTaskStatus(
        "task-1", "completed", result_count=2
    )
    retry = client.post(
        f"/api/v1/acquisition/jobs/{created['id']}/retry", headers=headers
    )
    assert retry.status_code == 202
    data = retry.json()["data"]
    assert data["retry_of_id"] == created["id"]
    assert data["attempt"] == 2


def test_retry_non_failed_returns_409(acquisition_override):
    client = TestClient(app)
    token = _token("acq-admin", "admin")
    headers = _headers(token)
    # The fake gateway completes immediately, so the first job is terminal
    # completed; retrying a completed job must be rejected.
    created = client.post(
        "/api/v1/acquisition/jobs",
        headers=headers,
        json={"source": "boss", "keyword": "Java", "city": "北京", "pages": 1},
    ).json()["data"]
    response = client.post(
        f"/api/v1/acquisition/jobs/{created['id']}/retry", headers=headers
    )
    assert response.status_code == 409
