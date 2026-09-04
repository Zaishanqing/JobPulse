"""Regression tests for DeepSeek transport retry behaviour."""

from __future__ import annotations

import httpx
import pytest
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)

from jobgraph_contracts.deepseek import (
    DeepSeekClient,
    DeepSeekInvalidRequestError,
    DeepSeekServerError,
)


class _FakeCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeClient:
    def __init__(self, completions):
        self.chat = type("Chat", (), {"completions": completions})()


def _server_error(status: int) -> InternalServerError:
    request = httpx.Request("POST", "https://example.invalid/chat/completions")
    return InternalServerError(
        "service unavailable",
        response=httpx.Response(status, request=request),
        body=None,
    )


def _client_with(completions, transport_attempts: int = 3):
    client = DeepSeekClient.__new__(DeepSeekClient)
    client.client = _FakeClient(completions)
    client.model = "deepseek-v4-flash"
    client.transport_attempts = transport_attempts
    client.retryable_exceptions = (
        APIConnectionError,
        APITimeoutError,
        RateLimitError,
        InternalServerError,
    )
    return client


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_server_error_is_retried_until_success(status):
    ok = object()
    completions = _FakeCompletions([_server_error(status), _server_error(status), ok])
    client = _client_with(completions)

    response, attempts = client._request("system", "user")

    assert response is ok
    assert attempts == 3
    assert completions.calls == 3


def test_server_error_exhausts_retries_and_maps_to_server_error():
    completions = _FakeCompletions([_server_error(503), _server_error(503)])
    client = _client_with(completions, transport_attempts=2)

    with pytest.raises(DeepSeekServerError):
        client._request("system", "user")
    assert completions.calls == 2


@pytest.mark.parametrize("status", [400, 409, 422])
def test_invalid_provider_request_is_non_retryable(status):
    request = httpx.Request("POST", "https://example.invalid/chat/completions")
    error = APIStatusError(
        "invalid request",
        response=httpx.Response(status, request=request),
        body=None,
    )
    client = DeepSeekClient.__new__(DeepSeekClient)
    client.model = "deepseek-v4-flash"

    mapped = client._map_openai_error(error)

    assert isinstance(mapped, DeepSeekInvalidRequestError)
    assert mapped.status_code == status
