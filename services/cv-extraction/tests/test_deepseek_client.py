import json
import socket
import ssl

import httpx
import pytest
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    NotFoundError,
    RateLimitError,
)

from jobgraph_contracts.deepseek import (
    DeepSeekAuthError,
    DeepSeekConnectionError,
    DeepSeekModelNotFoundError,
    DeepSeekRateLimitError,
    DeepSeekTimeoutError,
)
from src.deepseek_client import DeepSeekClient
from src.exceptions import InvalidJSONError


def test_parse_json_object_is_strict_and_does_not_extract_embedded_object():
    client = DeepSeekClient.__new__(DeepSeekClient)
    with pytest.raises(json.JSONDecodeError):
        client._parse_json_object('说明文字 {"skills": []}')


def test_parse_json_object_rejects_unescaped_quotes():
    client = DeepSeekClient.__new__(DeepSeekClient)
    with pytest.raises(json.JSONDecodeError):
        client._parse_json_object('{"content": "原文"Python""}')


def test_parse_json_object_rejects_duplicate_keys_instead_of_overwriting_data():
    client = DeepSeekClient.__new__(DeepSeekClient)

    with pytest.raises(InvalidJSONError, match="duplicate JSON object keys: project_experience"):
        client._parse_json_object(
            '{"project_experience":[{"name":"完整项目"}],"project_experience":null}'
        )


def test_transport_retry_count_is_reported(monkeypatch):
    class FakeCompletions:
        def __init__(self):
            self.calls = 0

        def create(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("slow provider response")
            return object()

    completions = FakeCompletions()
    client = DeepSeekClient.__new__(DeepSeekClient)
    client.model = "fake"
    client.retryable_exceptions = (TimeoutError,)
    client.client = type(
        "FakeOpenAI",
        (),
        {"chat": type("FakeChat", (), {"completions": completions})()},
    )()
    monkeypatch.setattr(
        "src.deepseek_client.wait_exponential",
        lambda **_kwargs: (lambda _retry_state: 0),
    )

    response, attempt_count = client._request("system", "user")

    assert response is not None
    assert attempt_count == 2


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.deepseek.com/chat/completions")


def test_base_url_is_read_from_shared_environment_variable(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://gateway.example.test/v1")

    client = DeepSeekClient(model="deepseek-v4-flash")

    assert str(client.client.base_url).rstrip("/") == "https://gateway.example.test/v1"


def test_default_base_url_is_official_deepseek(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)

    client = DeepSeekClient(model="deepseek-v4-flash")

    assert str(client.client.base_url).rstrip("/") == "https://api.deepseek.com"


def _mapped_client():
    client = DeepSeekClient.__new__(DeepSeekClient)
    client.model = "deepseek-v4-flash"
    return client


def test_openai_status_errors_are_mapped_to_typed_provider_errors():
    client = _mapped_client()
    request = _request()

    auth = client._map_openai_error(
        AuthenticationError(
            "incorrect api key",
            response=httpx.Response(401, request=request),
            body=None,
        )
    )
    assert isinstance(auth, DeepSeekAuthError)
    assert auth.code == "DEEPSEEK_AUTH_FAILED"

    missing = client._map_openai_error(
        NotFoundError(
            "model not found",
            response=httpx.Response(404, request=request),
            body=None,
        )
    )
    assert isinstance(missing, DeepSeekModelNotFoundError)
    assert missing.code == "DEEPSEEK_MODEL_NOT_AVAILABLE"
    assert "deepseek-v4-flash" in str(missing)

    limited = client._map_openai_error(
        RateLimitError(
            "rate limited",
            response=httpx.Response(429, request=request),
            body=None,
        )
    )
    assert isinstance(limited, DeepSeekRateLimitError)
    assert limited.code == "DEEPSEEK_RATE_LIMITED"

    timeout = client._map_openai_error(APITimeoutError(request=request))
    assert isinstance(timeout, DeepSeekTimeoutError)
    assert timeout.code == "DEEPSEEK_TIMEOUT"


def test_connection_errors_keep_dns_tls_and_generic_reasons():
    client = _mapped_client()
    request = _request()

    tls_error = APIConnectionError(message="Connection error.", request=request)
    tls_error.__cause__ = ssl.SSLError("tls failure")
    tls = client._map_openai_error(tls_error)
    assert isinstance(tls, DeepSeekConnectionError)
    assert tls.reason == "tls"

    dns_error = APIConnectionError(message="Connection error.", request=request)
    dns_error.__cause__ = socket.gaierror(-2, "Name or service not known")
    dns = client._map_openai_error(dns_error)
    assert isinstance(dns, DeepSeekConnectionError)
    assert dns.reason == "dns"

    generic = client._map_openai_error(
        APIConnectionError(message="Connection error.", request=request)
    )
    assert isinstance(generic, DeepSeekConnectionError)
    assert generic.reason == "connect"


def test_request_raises_mapped_provider_error(monkeypatch):
    request = _request()

    class Completions:
        def create(self, **_kwargs):
            raise AuthenticationError(
                "incorrect api key",
                response=httpx.Response(401, request=request),
                body=None,
            )

    client = DeepSeekClient.__new__(DeepSeekClient)
    client.model = "deepseek-v4-flash"
    client.retryable_exceptions = ()
    client.client = type(
        "FakeOpenAI",
        (),
        {"chat": type("FakeChat", (), {"completions": Completions()})()},
    )()

    with pytest.raises(DeepSeekAuthError) as exc_info:
        client._request("system", "user")

    assert exc_info.value.code == "DEEPSEEK_AUTH_FAILED"
