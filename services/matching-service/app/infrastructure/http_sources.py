"""HTTP adapters for future cross-service contract endpoints."""

from __future__ import annotations

import time
from contextlib import suppress
from urllib.parse import quote

import httpx

from app.ports.upstream_contracts import UpstreamResponseError, UpstreamTimeoutError


class _HttpProfileSource:
    def __init__(
        self,
        base_url: str,
        contract_path: str,
        *,
        timeout_seconds: float = 5.0,
        service_token: str | None = None,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.1,
        health_url: str | None = None,
    ) -> None:
        if timeout_seconds <= 0 or max_retries < 0 or retry_backoff_seconds < 0:
            raise ValueError("invalid HTTP source retry configuration")
        self._base_url = base_url.rstrip("/")
        self._contract_path = contract_path.strip("/")
        self._timeout = timeout_seconds
        self._service_token = service_token
        self._max_retries = max_retries
        self._backoff = retry_backoff_seconds
        self._health_url = health_url or f"{self._base_url}/health"

    def _fetch(self, identifier: str, *, contract_path: str | None = None) -> object:
        selected_path = contract_path or self._contract_path
        url = f"{self._base_url}/{selected_path}/{quote(identifier, safe='')}"
        response = self._request("GET", url)
        try:
            return response.json()
        except ValueError as exc:
            raise UpstreamResponseError(None, "upstream profile returned invalid JSON") from exc

    def check_health(self) -> None:
        self._request("GET", self._health_url)

    def _request(self, method: str, url: str) -> httpx.Response:
        headers = {"Accept": "application/json"}
        if self._service_token:
            headers["Authorization"] = f"Bearer {self._service_token}"
        for attempt in range(self._max_retries + 1):
            try:
                with httpx.Client(timeout=self._timeout) as client:
                    response = client.get(url, headers=headers)
                status_code = getattr(response, "status_code", 200)
                if (status_code == 429 or status_code >= 500) and attempt < self._max_retries:
                    self._wait(
                        attempt, getattr(response, "headers", {}).get("Retry-After")
                    )
                    continue
                response.raise_for_status()
                return response
            except httpx.TimeoutException as exc:
                if attempt < self._max_retries:
                    self._wait(attempt)
                    continue
                raise UpstreamTimeoutError(
                    f"timeout fetching {self._contract_path}"
                ) from exc
            except httpx.RequestError as exc:
                if attempt < self._max_retries:
                    self._wait(attempt)
                    continue
                raise UpstreamResponseError(None, "profile upstream is unavailable") from exc
            except httpx.HTTPStatusError as exc:
                raise UpstreamResponseError(
                    exc.response.status_code, "profile upstream rejected the request"
                ) from exc
        raise AssertionError("retry loop must return or raise")  # pragma: no cover

    def _wait(self, attempt: int, retry_after: str | None = None) -> None:
        delay = self._backoff * (2**attempt)
        if retry_after:
            with suppress(ValueError):
                delay = max(delay, min(float(retry_after), 60.0))
        if delay:
            time.sleep(delay)


class HttpCVProfileSource(_HttpProfileSource):
    def fetch_cv_profile(self, cv_id: str) -> object:
        return self._fetch(cv_id)


class HttpPositionProfileSource(_HttpProfileSource):
    def __init__(
        self,
        *args,
        enterprise_contract_path: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._enterprise_contract_path = (
            enterprise_contract_path.strip("/") if enterprise_contract_path else None
        )

    def fetch_position_profile(self, position_id: str) -> object:
        return self._fetch(position_id)

    def fetch_enterprise_job_profile(self, position_id: str) -> object:
        if self._enterprise_contract_path is None:
            return self._fetch(position_id)
        return self._fetch(position_id, contract_path=self._enterprise_contract_path)
