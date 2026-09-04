"""Manifest and digest checks for locally mounted production model artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class ModelArtifactError(ValueError):
    """Raised when a model directory does not match its signed-in-repo manifest."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_files(files: list[dict[str, Any]]) -> bytes:
    return json.dumps(
        sorted(files, key=lambda item: str(item["path"])),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def build_manifest(model_dir: str | Path, *, model_id: str, model_revision: str) -> dict[str, Any]:
    root = Path(model_dir).resolve()
    if not root.is_dir():
        raise ModelArtifactError(f"model directory does not exist: {root}")
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "manifest.json":
            continue
        relative = path.relative_to(root).as_posix()
        files.append({"path": relative, "size": path.stat().st_size, "sha256": _sha256(path)})
    if not files:
        raise ModelArtifactError("model directory contains no model files")
    artifact_sha256 = hashlib.sha256(_canonical_files(files)).hexdigest()
    return {
        "schema_version": "responsibility-ce-artifact.v1",
        "model_id": model_id,
        "model_revision": model_revision,
        "artifact_sha256": artifact_sha256,
        "files": files,
    }


def verify_manifest(model_dir: str | Path, manifest_path: str | Path | None = None) -> str:
    root = Path(model_dir).resolve()
    manifest_file = Path(manifest_path or root / "manifest.json").resolve()
    if root not in manifest_file.parents:
        raise ModelArtifactError("model manifest must be inside the model directory")
    if not manifest_file.is_file():
        raise ModelArtifactError(f"model manifest does not exist: {manifest_file}")
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelArtifactError(f"model manifest is unreadable: {manifest_file}") from exc
    if manifest.get("schema_version") != "responsibility-ce-artifact.v1":
        raise ModelArtifactError("unsupported responsibility CE model manifest")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise ModelArtifactError("model manifest has no files")
    actual = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ModelArtifactError("model manifest contains an invalid file entry")
        candidate = (root / entry["path"]).resolve()
        if root not in candidate.parents or not candidate.is_file():
            raise ModelArtifactError(f"model manifest file is missing: {entry['path']}")
        actual.append({
            "path": entry["path"],
            "size": candidate.stat().st_size,
            "sha256": _sha256(candidate),
        })
    digest = hashlib.sha256(_canonical_files(actual)).hexdigest()
    if digest != manifest.get("artifact_sha256"):
        raise ModelArtifactError("model artifact digest does not match manifest")
    return digest
