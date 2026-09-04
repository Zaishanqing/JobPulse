"""Shared DeepSeek chat client used by extraction and matching services.

The matching service consumes the same DEEPSEEK_API_KEY and OpenAI-compatible
DeepSeek endpoint as the extraction pipeline.  This module intentionally has no
embedding surface: DeepSeek is used for LLM candidate generation only.
Transport retries cover connection errors, timeouts, rate limits, and 5xx
server errors so transient upstream failures do not abort a batch.
"""

from __future__ import annotations

import json
import os
import socket
import ssl
from dataclasses import dataclass
from typing import Any

from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential


DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class DeepSeekClientError(Exception):
    """Base class for shared DeepSeek client failures."""

    code = "DEEPSEEK_CLIENT_ERROR"


class MissingAPIKeyError(DeepSeekClientError):
    """Raised when DEEPSEEK_API_KEY is not configured."""

    code = "DEEPSEEK_API_KEY_MISSING"


class InvalidJSONError(DeepSeekClientError):
    """Raised when the model response is not a valid JSON object."""

    code = "DEEPSEEK_RESPONSE_INVALID"

    def __init__(
        self,
        message: str,
        raw_response: str | None = None,
    ):
        super().__init__(message)
        self.raw_response = raw_response


class DeepSeekTimeoutError(DeepSeekClientError):
    """Raised when the provider request exceeds the configured timeout."""

    code = "DEEPSEEK_TIMEOUT"


class DeepSeekConnectionError(DeepSeekClientError):
    """Raised for DNS, TLS, or generic connection failures."""

    code = "DEEPSEEK_CONNECTION_FAILED"

    def __init__(self, message: str, *, reason: str = "connect"):
        super().__init__(message)
        self.reason = reason


class DeepSeekHTTPStatusError(DeepSeekClientError):
    """Raised when the provider returns a non-2xx HTTP status."""

    code = "DEEPSEEK_PROVIDER_UNAVAILABLE"

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        model: str | None = None,
        response_body: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.model = model
        self.response_body = response_body


class DeepSeekAuthError(DeepSeekHTTPStatusError):
    """Raised for 401/403 provider responses."""

    code = "DEEPSEEK_AUTH_FAILED"


class DeepSeekModelNotFoundError(DeepSeekHTTPStatusError):
    """Raised when the provider does not expose the configured model."""

    code = "DEEPSEEK_MODEL_NOT_AVAILABLE"


class DeepSeekInvalidRequestError(DeepSeekHTTPStatusError):
    """Raised for non-retryable provider request validation errors."""

    code = "DEEPSEEK_INVALID_PROVIDER_REQUEST"


class DeepSeekRateLimitError(DeepSeekHTTPStatusError):
    """Raised for 429 provider responses."""

    code = "DEEPSEEK_RATE_LIMITED"


class DeepSeekServerError(DeepSeekHTTPStatusError):
    """Raised for provider-side server failures."""

    code = "DEEPSEEK_PROVIDER_UNAVAILABLE"


@dataclass
class DeepSeekResult:
    data: dict[str, Any]
    raw_response: str
    transport_attempt_count: int = 1


class DeepSeekClient:
    """OpenAI-compatible DeepSeek chat client with strict JSON output parsing."""

    def __init__(
        self,
        model: str,
        timeout: int = 300,
        *,
        transport_attempts: int = 3,
        json_attempts: int = 2,
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        from openai import (
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
            OpenAI,
            RateLimitError,
        )

        resolved_api_key = os.environ.get("DEEPSEEK_API_KEY") if api_key is None else api_key
        if not resolved_api_key:
            raise MissingAPIKeyError("Environment variable DEEPSEEK_API_KEY is required.")
        resolved_base_url = (
            os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_DEEPSEEK_BASE_URL
            if base_url is None
            else base_url
        ).rstrip("/")
        self.model = model
        self.base_url = resolved_base_url
        if transport_attempts < 1 or json_attempts < 1:
            raise ValueError("DeepSeek attempt limits must be positive")
        self.transport_attempts = transport_attempts
        self.json_attempts = json_attempts
        self.retryable_exceptions = (
            APIConnectionError,
            APITimeoutError,
            RateLimitError,
            InternalServerError,
        )
        self.client = OpenAI(
            api_key=resolved_api_key,
            base_url=resolved_base_url,
            timeout=timeout,
            max_retries=0,
        )

    @staticmethod
    def _connection_reason(exc: BaseException) -> str:
        cause = exc.__cause__
        while cause is not None:
            if isinstance(cause, ssl.SSLError):
                return "tls"
            if isinstance(cause, socket.gaierror):
                return "dns"
            cause = cause.__cause__
        return "connect"

    def _map_openai_error(self, exc: BaseException) -> BaseException:
        from openai import (
            APIConnectionError,
            APITimeoutError,
            APIStatusError,
            AuthenticationError,
            InternalServerError,
            NotFoundError,
            PermissionDeniedError,
            RateLimitError,
        )

        if isinstance(exc, DeepSeekClientError):
            return exc
        if isinstance(exc, APITimeoutError):
            return DeepSeekTimeoutError("DeepSeek request timed out")
        if isinstance(exc, APIConnectionError):
            reason = self._connection_reason(exc)
            return DeepSeekConnectionError(
                f"DeepSeek connection failed ({reason})",
                reason=reason,
            )
        if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
            return DeepSeekAuthError("DeepSeek API authentication failed")
        if isinstance(exc, NotFoundError):
            return DeepSeekModelNotFoundError(
                f"DeepSeek model '{self.model}' is not available",
                status_code=404,
                model=self.model,
            )
        if isinstance(exc, RateLimitError):
            return DeepSeekRateLimitError("DeepSeek API rate limit reached")
        if isinstance(exc, InternalServerError):
            return DeepSeekServerError("DeepSeek API server error")
        if isinstance(exc, APIStatusError):
            if exc.status_code in {400, 409, 422}:
                return DeepSeekInvalidRequestError(
                    f"DeepSeek API rejected the request (HTTP {exc.status_code})",
                    status_code=exc.status_code,
                    model=self.model,
                )
            return DeepSeekServerError(
                f"DeepSeek API returned HTTP {exc.status_code}"
            )
        return exc

    def _request(self, system_prompt: str, user_prompt: str):
        request_attempt_count = 0

        def count_attempt(_retry_state: object) -> None:
            nonlocal request_attempt_count
            request_attempt_count += 1

        # Some compatibility tests construct the client with ``__new__`` to
        # exercise error mapping without opening a real provider session.
        # Preserve the historical shared-client default for those lightweight
        # instances; production callers still pass their explicit limit.
        transport_attempts = getattr(self, "transport_attempts", 3)
        retryer = Retrying(
            stop=stop_after_attempt(transport_attempts),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type(self.retryable_exceptions),
            reraise=True,
            before=count_attempt,
        )
        try:
            response = retryer(
                self.client.chat.completions.create,
                model=self.model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except Exception as exc:
            mapped = self._map_openai_error(exc)
            if mapped is exc:
                raise
            raise mapped from exc
        return response, request_attempt_count

    def _parse_json_object(self, content: str) -> dict[str, Any]:
        def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            data: dict[str, Any] = {}
            duplicates: list[str] = []
            for key, value in pairs:
                if key in data and key not in duplicates:
                    duplicates.append(key)
                data[key] = value
            if duplicates:
                raise InvalidJSONError(
                    "DeepSeek output contains duplicate JSON object keys: "
                    + ", ".join(duplicates)
                )
            return data

        data = json.loads(content, object_pairs_hook=reject_duplicate_keys)
        if not isinstance(data, dict):
            raise InvalidJSONError(
                "DeepSeek output must parse into a JSON object.",
                raw_response=content,
            )
        return data

    def extract(self, system_prompt: str, user_prompt: str) -> DeepSeekResult:
        contents: list[str] = []
        parse_error: json.JSONDecodeError | None = None
        transport_attempt_count = 0
        for attempt in range(self.json_attempts):
            request_user_prompt = user_prompt
            if attempt > 0:
                error_detail = (
                    f"上一轮 JSON 解析错误位于第 {parse_error.lineno} 行第 {parse_error.colno} 列：{parse_error.msg}。"
                    if parse_error is not None
                    else "上一轮返回不是合法 JSON object。"
                )
                request_user_prompt = (
                    f"{user_prompt}\n\n"
                    f"{error_detail}请从头重新输出一个合法 JSON object；"
                    "字符串内部引用原文时只能使用中文引号“”，不得使用未转义的英文双引号；"
                    "不要输出 Markdown、解释、注释或代码块。"
                )
            response, current_attempt_count = self._request(
                system_prompt, request_user_prompt
            )
            transport_attempt_count += current_attempt_count
            content = response.choices[0].message.content
            if content is None:
                content = ""
            contents.append(content)
            try:
                data = self._parse_json_object(content)
                return DeepSeekResult(
                    data=data,
                    raw_response=content,
                    transport_attempt_count=transport_attempt_count,
                )
            except json.JSONDecodeError as exc:
                parse_error = exc
                if attempt + 1 < self.json_attempts:
                    continue
                raise InvalidJSONError(
                    "DeepSeek output is not valid JSON after retry.",
                    raw_response="\n\n--- retry boundary ---\n\n".join(contents),
                ) from exc
            except InvalidJSONError:
                if attempt + 1 < self.json_attempts:
                    continue
                raise InvalidJSONError(
                    "DeepSeek output is not valid JSON after retry.",
                    raw_response="\n\n--- retry boundary ---\n\n".join(contents),
                )

        raise InvalidJSONError(
            "DeepSeek output is not valid JSON after retry.",
            raw_response="\n\n--- retry boundary ---\n\n".join(contents),
        )


__all__ = [
    "DEFAULT_DEEPSEEK_BASE_URL",
    "DeepSeekClient",
    "DeepSeekAuthError",
    "DeepSeekClientError",
    "DeepSeekConnectionError",
    "DeepSeekHTTPStatusError",
    "DeepSeekInvalidRequestError",
    "DeepSeekModelNotFoundError",
    "DeepSeekResult",
    "DeepSeekRateLimitError",
    "DeepSeekServerError",
    "DeepSeekTimeoutError",
    "InvalidJSONError",
    "MissingAPIKeyError",
]
