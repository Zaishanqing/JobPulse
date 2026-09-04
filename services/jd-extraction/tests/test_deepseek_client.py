import json

import pytest
import openai

from src.deepseek_client import DeepSeekClient, MissingAPIKeyError


def test_explicit_runtime_configuration_overrides_process_environment(monkeypatch):
    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://environment.example")
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)

    client = DeepSeekClient(
        "configured-model",
        api_key="saved-key",
        base_url="https://saved.example/v1/",
    )

    assert client.base_url == "https://saved.example/v1"
    assert captured["api_key"] == "saved-key"
    assert captured["base_url"] == "https://saved.example/v1"

    with pytest.raises(MissingAPIKeyError):
        DeepSeekClient("configured-model", api_key="", base_url="https://saved.example/v1")


def test_parse_json_object_is_strict_and_does_not_extract_embedded_object():
    client = DeepSeekClient.__new__(DeepSeekClient)

    with pytest.raises(json.JSONDecodeError):
        client._parse_json_object('说明文字 {"jd_id": "jd_1"}')


def test_parse_json_object_rejects_unescaped_quotes_inside_derivation():
    client = DeepSeekClient.__new__(DeepSeekClient)

    with pytest.raises(json.JSONDecodeError):
        client._parse_json_object('{"derivation": "原文"AI Agent"是其中一项"}')
