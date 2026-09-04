"""HTTP adapter for the crawler internal service API."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import httpx

from app.contexts.acquisition.application import (
    AcquisitionCrawlFailed,
    AcquisitionError,
    AcquisitionExportFailed,
    AcquisitionLoginRequired,
    AcquisitionSourceUnavailable,
)
from app.contexts.acquisition.ports import (
    BossLoginStatus,
    BundleRef,
    CrawlerGateway,
    CrawlerSourceStatus,
    CrawlerTaskRef,
    CrawlerTaskStatus,
    LiepinLoginStatus,
)


class HttpCrawlerGateway:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        connect_timeout_seconds: float = 3.0,
        read_timeout_seconds: float = 120.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(connect_timeout_seconds, read=read_timeout_seconds)
        )

    def list_sources(self) -> list[CrawlerSourceStatus]:
        data = self._request("GET", "/internal/v1/sources")
        sources = data.get("sources", [])
        if not isinstance(sources, list):
            raise AcquisitionSourceUnavailable("Crawler source response is invalid")
        return [
            CrawlerSourceStatus(
                source=str(item.get("source", "")),
                available=bool(item.get("available", False)),
                ready=bool(item.get("ready", False)),
                login_required=bool(item.get("login_required", False)),
                reason=str(item["reason"]) if item.get("reason") else None,
            )
            for item in sources
            if isinstance(item, Mapping)
        ]

    def save_boss_cookies(self, cookies: list[dict]) -> dict:
        return self._request("POST", "/internal/v1/boss/cookies", json={"cookies": cookies})

    def save_liepin_cookies(self, cookies: list[dict]) -> dict:
        return self._request("POST", "/internal/v1/liepin/cookies", json={"cookies": cookies})

    def get_boss_login_status(self) -> BossLoginStatus:
        data = self._request("GET", "/internal/v1/boss/login/status")
        return BossLoginStatus(
            logged_in=bool(data.get("logged_in", False)),
            cookie_count=int(data.get("cookie_count", 0) or 0),
            running=bool(data.get("running", False)),
            status=str(data.get("status", "idle")),
            login_id=str(data["login_id"]) if data.get("login_id") else None,
            started_at=str(data["started_at"]) if data.get("started_at") else None,
            finished_at=str(data["finished_at"]) if data.get("finished_at") else None,
            message=str(data["message"]) if data.get("message") else None,
            updated_at=str(data["updated_at"]) if data.get("updated_at") else None,
        )

    def get_liepin_login_status(self) -> LiepinLoginStatus:
        data = self._request("GET", "/internal/v1/liepin/login/status")
        return LiepinLoginStatus(
            logged_in=bool(data.get("logged_in", False)),
            cookie_count=int(data.get("cookie_count", 0) or 0),
            running=bool(data.get("running", False)),
            status=str(data.get("status", "idle")),
            login_id=str(data["login_id"]) if data.get("login_id") else None,
            started_at=str(data["started_at"]) if data.get("started_at") else None,
            finished_at=str(data["finished_at"]) if data.get("finished_at") else None,
            message=str(data["message"]) if data.get("message") else None,
            updated_at=str(data["updated_at"]) if data.get("updated_at") else None,
        )

    def start_crawl(
        self,
        *,
        source: str,
        keyword: str,
        city: str,
        pages: int,
    ) -> CrawlerTaskRef:
        data = self._request(
            "POST",
            "/internal/v1/crawl",
            json={
                "source": source,
                "keyword": keyword,
                "city": city,
                "pages": pages,
            },
        )
        task_id = data.get("task_id")
        if not task_id:
            raise AcquisitionSourceUnavailable("Crawler did not return a task id")
        return CrawlerTaskRef(task_id=str(task_id))

    def get_task(self, task_id: str) -> CrawlerTaskStatus:
        data = self._request("GET", f"/internal/v1/tasks/{task_id}")
        return CrawlerTaskStatus(
            task_id=str(data.get("task_id", task_id)),
            status=str(data.get("status", "running")),
            result_count=int(data.get("result_count", 0) or 0),
            progress=str(data["progress"]) if data.get("progress") else None,
            error_message=str(data["error_message"])
            if data.get("error_message")
            else None,
        )

    def export_bundle(self, *, task_id: str, source: str) -> BundleRef:
        data = self._request(
            "POST",
            "/internal/v1/export",
            json={"task_id": task_id, "source": source},
        )
        bundle_id = data.get("bundle_id")
        file_name = data.get("file_name")
        if not bundle_id or not file_name:
            raise AcquisitionExportFailed("Crawler export response is invalid")
        return BundleRef(
            bundle_id=str(bundle_id),
            file_name=str(file_name),
            record_count=int(data.get("record_count", 0) or 0),
            hash=str(data["hash"]) if data.get("hash") else None,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            response = self._client.request(
                method,
                f"{self._base_url}{path}",
                headers=headers,
                json=json,
            )
        except httpx.TimeoutException as exc:
            raise AcquisitionSourceUnavailable("Crawler request timed out") from exc
        except httpx.RequestError as exc:
            raise AcquisitionSourceUnavailable("Crawler service is unavailable") from exc
        if response.status_code >= 500:
            raise AcquisitionSourceUnavailable("Crawler service is unavailable")
        if response.status_code == 401 or response.status_code == 403:
            raise AcquisitionSourceUnavailable("Crawler service authentication failed")
        if response.status_code >= 400:
            self._raise_upstream_error(response)
        try:
            body = response.json()
        except ValueError as exc:
            raise AcquisitionSourceUnavailable("Crawler returned invalid JSON") from exc
        if isinstance(body, dict) and body.get("data") is not None:
            return body["data"] if isinstance(body["data"], dict) else {"value": body["data"]}
        return body if isinstance(body, dict) else {"value": body}

    @staticmethod
    def _raise_upstream_error(response: httpx.Response) -> None:
        try:
            body = response.json()
        except ValueError:
            body = {}
        detail = body.get("detail") if isinstance(body, dict) else None
        if isinstance(detail, dict):
            code = str(detail.get("error_code") or detail.get("code") or "")
            message = str(detail.get("error_message") or detail.get("message") or "")
        else:
            code = ""
            message = str(detail or body.get("message") or "crawler request failed")
        mapping = {
            "source_unavailable": AcquisitionSourceUnavailable,
            "login_required": AcquisitionLoginRequired,
            "crawl_failed": AcquisitionCrawlFailed,
            "export_failed": AcquisitionExportFailed,
        }
        error_type = mapping.get(code, AcquisitionSourceUnavailable)
        raise error_type(message or "crawler request failed")


class LocalBundleStore:
    """Resolve crawler-produced bundle filenames inside a shared bundle volume."""

    def __init__(self, bundle_dir: str) -> None:
        self._bundle_dir = Path(bundle_dir).resolve()

    def resolve(self, bundle: BundleRef) -> Path:
        candidate = (self._bundle_dir / bundle.file_name).resolve()
        try:
            candidate.relative_to(self._bundle_dir)
        except ValueError as exc:
            raise AcquisitionExportFailed(
                f"Bundle file escapes bundle directory: {bundle.file_name}"
            ) from exc
        if candidate.suffix.lower() != ".zip":
            raise AcquisitionExportFailed(
                f"Bundle file must be a .zip archive: {bundle.file_name}"
            )
        if not candidate.is_file():
            raise AcquisitionExportFailed(f"Bundle file is not available: {bundle.file_name}")
        return candidate
