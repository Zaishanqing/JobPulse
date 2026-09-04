from __future__ import annotations

import json

import pytest

from app.application.model_artifact import ModelArtifactError, build_manifest, verify_manifest


def test_build_and_verify_manifest_returns_stable_digest(tmp_path) -> None:
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "weights.bin").write_bytes(b"weights")

    manifest = build_manifest(tmp_path, model_id="ce-v1", model_revision="rev-1")
    (tmp_path / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    assert verify_manifest(tmp_path) == manifest["artifact_sha256"]


def test_verify_manifest_rejects_tampered_file(tmp_path) -> None:
    (tmp_path / "weights.bin").write_bytes(b"weights")
    manifest = build_manifest(tmp_path, model_id="ce-v1", model_revision="rev-1")
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "weights.bin").write_bytes(b"tampered")

    with pytest.raises(ModelArtifactError, match="digest"):
        verify_manifest(tmp_path)


def test_verify_manifest_rejects_path_escape(tmp_path) -> None:
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "responsibility-ce-artifact.v1",
                "artifact_sha256": "x",
                "files": [{"path": "../outside.bin", "size": 1, "sha256": "x"}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ModelArtifactError, match="missing"):
        verify_manifest(tmp_path)
