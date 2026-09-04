from __future__ import annotations

import json

import pytest

from app.application.model_artifact import build_manifest
from app.bootstrap.application import build_responsibility_verifier


def test_responsibility_ce_is_explicitly_disabled_by_default() -> None:
    assert build_responsibility_verifier({}, runtime_mode="production") is None


def test_responsibility_ce_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="MATCHING_RESPONSIBILITY_CE_MODE"):
        build_responsibility_verifier(
            {"MATCHING_RESPONSIBILITY_CE_MODE": "shadow"},
            runtime_mode="production",
        )


def test_enabled_production_ce_requires_model_and_embedding_contract() -> None:
    with pytest.raises(ValueError, match="configuration is incomplete"):
        build_responsibility_verifier(
            {"MATCHING_RESPONSIBILITY_CE_MODE": "enabled"},
            runtime_mode="production",
        )


def test_production_ce_rejects_rule_fallback(tmp_path) -> None:
    with pytest.raises(ValueError, match="cannot enable"):
        build_responsibility_verifier(
            {
                "MATCHING_RESPONSIBILITY_CE_MODE": "enabled",
                "MATCHING_RESPONSIBILITY_CE_MODEL_PATH": str(tmp_path),
                "MATCHING_RESPONSIBILITY_CE_EMBEDDING_URL": "http://embedding:8000",
                "MATCHING_RESPONSIBILITY_CE_FALLBACK_TO_RULES": "true",
            },
            runtime_mode="production",
        )


def test_enabled_ce_is_injected_with_frozen_runtime_contract(tmp_path, monkeypatch) -> None:
    captured = {}
    (tmp_path / "model.safetensors").write_bytes(b"frozen-test-model")
    manifest = build_manifest(tmp_path, model_id="test-ce", model_revision="test-rev")
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    class FakeVerifier:
        model_loaded = True
        model_load_error = None

        def __init__(self, config):
            captured["config"] = config

    monkeypatch.setattr("app.bootstrap.application.ResponsibilityCEVerifier", FakeVerifier)
    verifier = build_responsibility_verifier(
        {
            "MATCHING_RESPONSIBILITY_CE_MODE": "enabled",
            "MATCHING_RESPONSIBILITY_CE_MODEL_PATH": str(tmp_path),
            "MATCHING_RESPONSIBILITY_CE_EMBEDDING_URL": "http://embedding:8000",
            "MATCHING_RESPONSIBILITY_CE_MANIFEST_PATH": str(tmp_path / "manifest.json"),
        },
        runtime_mode="production",
    )

    assert isinstance(verifier, FakeVerifier)
    config = captured["config"]
    assert config.threshold == 1.098377
    assert config.top_k == 3
    assert config.embedding_url == "http://embedding:8000"
    assert config.fallback_to_rules is False
    assert config.artifact_digest


def test_production_ce_rejects_missing_manifest(tmp_path, monkeypatch) -> None:
    class FakeVerifier:
        model_loaded = True
        model_load_error = None

        def __init__(self, config):
            pass

    monkeypatch.setattr("app.bootstrap.application.ResponsibilityCEVerifier", FakeVerifier)
    with pytest.raises(ValueError, match="artifact invalid"):
        build_responsibility_verifier(
            {
                "MATCHING_RESPONSIBILITY_CE_MODE": "enabled",
                "MATCHING_RESPONSIBILITY_CE_MODEL_PATH": str(tmp_path),
                "MATCHING_RESPONSIBILITY_CE_EMBEDDING_URL": "http://embedding:8000",
            },
            runtime_mode="production",
        )
