from __future__ import annotations

import httpx
import pytest

from app.contexts.acquisition.application import (
    AcquisitionExportFailed,
    AcquisitionLoginRequired,
    AcquisitionSourceUnavailable,
)
from app.contexts.acquisition.ports import BundleRef
from app.infrastructure.crawler_gateway import HttpCrawlerGateway, LocalBundleStore


def _gateway(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return HttpCrawlerGateway(
        base_url="http://crawler.test",
        token="secret-token",
        client=client,
    )


def test_sources_maps_contract():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret-token"
        return httpx.Response(
            200,
            json={
                "data": {
                    "sources": [
                        {"source": "boss", "available": True, "ready": True, "login_required": False, "reason": None},
                        {"source": "liepin", "available": True, "ready": False, "login_required": True, "reason": "login"},
                        {"source": "feishu", "available": True, "ready": True, "login_required": False, "reason": None},
                    ]
                }
            },
        )

    sources = _gateway(handler).list_sources()
    assert [(item.source, item.ready, item.login_required) for item in sources] == [
        ("boss", True, False),
        ("liepin", False, True),
        ("feishu", True, False),
    ]


def test_cookies_endpoints_map_contract():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/v1/boss/cookies":
            return httpx.Response(200, json={"data": {"saved": True, "count": 2, "verified": True}})
        if request.url.path == "/internal/v1/liepin/cookies":
            return httpx.Response(200, json={"data": {"saved": True, "count": 1, "verified": True}})
        raise AssertionError(f"Unexpected path {request.url.path}")

    gateway = _gateway(handler)
    assert gateway.save_boss_cookies([{"name": "wt2"}]) == {
        "saved": True,
        "count": 2,
        "verified": True,
    }
    assert gateway.save_liepin_cookies([{"name": "lg"}])["count"] == 1


def test_crawl_task_and_export_map_contract():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/v1/crawl":
            return httpx.Response(200, json={"data": {"task_id": "task-9"}})
        if request.url.path == "/internal/v1/tasks/task-9":
            return httpx.Response(
                200,
                json={"data": {"task_id": "task-9", "status": "completed", "result_count": 5, "progress": "done", "error_message": None}},
            )
        if request.url.path == "/internal/v1/export":
            return httpx.Response(
                200,
                json={"data": {"bundle_id": "bundle-x", "file_name": "bundle-x.zip", "record_count": 5, "hash": "abc"}},
            )
        raise AssertionError(f"Unexpected path {request.url.path}")

    gateway = _gateway(handler)
    task = gateway.start_crawl(source="boss", keyword="Java", city="北京", pages=2)
    assert task.task_id == "task-9"
    status = gateway.get_task(task.task_id)
    assert status.status == "completed"
    assert status.result_count == 5
    bundle = gateway.export_bundle(task_id=task.task_id, source="boss")
    assert bundle.bundle_id == "bundle-x"
    assert bundle.hash == "abc"


def test_upstream_422_login_required_maps():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={"detail": {"error_code": "login_required", "error_message": "please login"}},
        )

    with pytest.raises(AcquisitionLoginRequired):
        _gateway(handler).start_crawl(source="boss", keyword="Java", city="北京", pages=1)


def test_unavailable_maps_to_source_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"message": "down"})

    with pytest.raises(AcquisitionSourceUnavailable):
        _gateway(handler).list_sources()


def test_local_bundle_store_rejects_directory_escape(tmp_path):
    bundle_dir = tmp_path / "bundles"
    bundle_dir.mkdir()
    outside = tmp_path / "outside.zip"
    outside.write_bytes(b"not-a-real-bundle")
    store = LocalBundleStore(str(bundle_dir))

    with pytest.raises(AcquisitionExportFailed, match="escapes bundle directory"):
        store.resolve(BundleRef("bundle-1", "../outside.zip", 0, None))


def test_local_bundle_store_rejects_non_zip(tmp_path):
    bundle_dir = tmp_path / "bundles"
    bundle_dir.mkdir()
    (bundle_dir / "bundle.txt").write_text("x")
    store = LocalBundleStore(str(bundle_dir))

    with pytest.raises(AcquisitionExportFailed, match="must be a .zip"):
        store.resolve(BundleRef("bundle-1", "bundle.txt", 0, None))


def test_local_bundle_store_accepts_contained_zip(tmp_path):
    bundle_dir = tmp_path / "bundles"
    bundle_dir.mkdir()
    bundle_path = bundle_dir / "bundle.zip"
    bundle_path.write_bytes(b"zip")
    store = LocalBundleStore(str(bundle_dir))

    assert store.resolve(BundleRef("bundle-1", "bundle.zip", 0, None)) == bundle_path.resolve()
