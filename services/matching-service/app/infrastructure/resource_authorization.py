"""Development/test and HTTP adapters for trusted resource authorization."""

from __future__ import annotations

import time
from collections.abc import Iterable
from contextlib import suppress
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.domain.auth import AuthContext
from app.ports.upstream_contracts import UpstreamResponseError, UpstreamTimeoutError


class InMemoryCVAuthorizationAdapter:
    def __init__(self, ownerships: Iterable[tuple[str, str, str]] = ()) -> None:
        self._ownerships = frozenset(ownerships)

    def is_owner(self, context: AuthContext, cv_id: str) -> bool:
        return (context.tenant_id, context.subject_id, cv_id) in self._ownerships


class InMemoryApplicationGrantAdapter:
    def __init__(self, grants: Iterable[tuple[str, str, str, str]] = ()) -> None:
        self._grants = frozenset(grants)

    def has_active_grant(
        self, context: AuthContext, cv_id: str, position_id: str
    ) -> bool:
        return (context.tenant_id, context.subject_id, cv_id, position_id) in self._grants


class AllowAllCVAuthorizationAdapter:
    """Explicit development/test adapter; never selected in production."""

    def is_owner(self, context: AuthContext, cv_id: str) -> bool:
        return True


class AllowAllApplicationGrantAdapter:
    """Explicit development/test adapter; never selected in production."""

    def has_active_grant(
        self, context: AuthContext, cv_id: str, position_id: str
    ) -> bool:
        return True


class _HttpAuthorizationAdapter:
    def __init__(
        self,
        url: str,
        *,
        service_token: str,
        timeout_seconds: float = 5.0,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.1,
        health_url: str | None = None,
    ) -> None:
        if not url.strip() or not service_token.strip():
            raise ValueError("authorization URL and service token are required")
        if timeout_seconds <= 0 or max_retries < 0 or retry_backoff_seconds < 0:
            raise ValueError("invalid authorization retry configuration")
        self._url = url
        self._token = service_token
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._backoff = retry_backoff_seconds
        parts = urlsplit(url)
        self._health_url = health_url or urlunsplit(
            (parts.scheme, parts.netloc, "/health", "", "")
        )

    def _authorized(self, payload: dict[str, str]) -> bool:
        response = self._request("POST", self._url, payload=payload)
        if response.status_code == 404:
            return False
        try:
            body = response.json()
        except ValueError as exc:
            raise UpstreamResponseError(None, "authorization response is invalid") from exc
        if not isinstance(body, dict):
            raise UpstreamResponseError(None, "authorization response is invalid")
        data = body.get("data", body)
        if not isinstance(data, dict) or not isinstance(data.get("authorized"), bool):
            raise UpstreamResponseError(None, "authorization response is invalid")
        return data["authorized"]

    def check_health(self) -> None:
        self._request("GET", self._health_url)

    def _request(
        self, method: str, url: str, *, payload: object | None = None
    ) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            try:
                headers = {
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self._token}",
                }
                if method == "POST":
                    response = httpx.post(
                        url, json=payload, headers=headers, timeout=self._timeout
                    )
                else:
                    response = httpx.get(url, headers=headers, timeout=self._timeout)
                if response.status_code == 404:
                    return response
                if (
                    response.status_code == 429 or response.status_code >= 500
                ) and attempt < self._max_retries:
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
                raise UpstreamTimeoutError("authorization service timed out") from exc
            except httpx.RequestError as exc:
                if attempt < self._max_retries:
                    self._wait(attempt)
                    continue
                raise UpstreamResponseError(
                    None, "authorization service is unavailable"
                ) from exc
            except httpx.HTTPStatusError as exc:
                raise UpstreamResponseError(
                    exc.response.status_code, "authorization service failed"
                ) from exc
        raise AssertionError("retry loop must return or raise")  # pragma: no cover

    def _wait(self, attempt: int, retry_after: str | None = None) -> None:
        delay = self._backoff * (2**attempt)
        if retry_after:
            with suppress(ValueError):
                delay = max(delay, min(float(retry_after), 60.0))
        if delay:
            time.sleep(delay)


class HttpCVAuthorizationAdapter(_HttpAuthorizationAdapter):
    def is_owner(self, context: AuthContext, cv_id: str) -> bool:
        return self._authorized(
            {"tenant_id": context.tenant_id, "subject_id": context.subject_id, "cv_id": cv_id}
        )


class HttpApplicationGrantAdapter(_HttpAuthorizationAdapter):
    def has_active_grant(
        self, context: AuthContext, cv_id: str, position_id: str
    ) -> bool:
        return self._authorized(
            {
                "tenant_id": context.tenant_id,
                "subject_id": context.subject_id,
                "cv_id": cv_id,
                "position_id": position_id,
            }
        )


class HttpEnterpriseJobGrantAdapter(_HttpAuthorizationAdapter):
    def has_active_grant(
        self, context: AuthContext, cv_id: str, position_id: str
    ) -> bool:
        # 任务画像中的企业岗位身份是 enterprise_job:<job_id>，而上游授权契约
        # 按原始 enterprise_job_id 查询投递授权；发送前必须剥离前缀。
        raw_job_id = position_id.removeprefix("enterprise_job:")
        return self._authorized(
            {
                "tenant_id": context.tenant_id,
                "subject_id": context.subject_id,
                "cv_id": cv_id,
                "enterprise_job_id": raw_job_id,
            }
        )
