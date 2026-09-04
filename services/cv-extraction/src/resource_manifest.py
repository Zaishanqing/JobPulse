from __future__ import annotations

import json
import hashlib
from pathlib import Path

MANIFEST_SCHEMA = "cv-resource-manifest.v1"


class ResourceManifestError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def validate_resource_manifest(
    manifest_path: str | Path,
    *,
    normalization_path: str | Path,
    taxonomy_path: str | Path,
    normalization_version: str,
    cv_schema_version: str,
) -> dict:
    manifest_file = Path(manifest_path)
    if not manifest_file.is_file():
        raise ResourceManifestError(
            "CV_NORMALIZATION_RESOURCE_MISSING",
            "CV resource manifest does not exist",
        )
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ResourceManifestError(
            "CV_NORMALIZATION_RESOURCE_MISSING",
            "CV resource manifest is not valid JSON",
        ) from exc
    if not isinstance(manifest, dict) or manifest.get("manifest_schema_version") != MANIFEST_SCHEMA:
        raise ResourceManifestError(
            "CV_NORMALIZATION_RESOURCE_MISSING",
            f"CV resource manifest must use schema {MANIFEST_SCHEMA}",
        )
    declared_version = manifest.get("normalization_version")
    if declared_version != normalization_version:
        raise ResourceManifestError(
            "CV_NORMALIZATION_RESOURCE_MISSING",
            "CV normalization version does not match the resource manifest",
        )
    compatible = manifest.get("compatible_cv_schema_versions")
    if not isinstance(compatible, list) or cv_schema_version not in compatible:
        raise ResourceManifestError(
            "CV_NORMALIZATION_RESOURCE_MISSING",
            f"CV schema version {cv_schema_version} is not compatible with the resource manifest",
        )
    root = manifest_file.resolve().parent
    configured = {
        "normalization": Path(normalization_path),
        "taxonomy": Path(taxonomy_path),
    }
    for kind, relative_key in (
        ("normalization", "normalization_relative_path"),
        ("taxonomy", "taxonomy_relative_path"),
    ):
        relative = manifest.get(relative_key)
        if not isinstance(relative, str) or not relative:
            raise ResourceManifestError(
                "CV_NORMALIZATION_RESOURCE_MISSING",
                f"CV resource manifest misses {relative_key}",
            )
        resolved = (root / relative).resolve()
        if root not in resolved.parents:
            raise ResourceManifestError(
                "CV_NORMALIZATION_RESOURCE_MISSING",
                f"CV resource path escapes resource root: {relative_key}",
            )
        if resolved != configured[kind].resolve():
            raise ResourceManifestError(
                "CV_NORMALIZATION_RESOURCE_MISSING",
                f"Configured {kind} path does not match the resource manifest",
            )
        if not resolved.is_file():
            raise ResourceManifestError(
                "CV_NORMALIZATION_RESOURCE_MISSING",
                f"CV resource file does not exist: {resolved}",
            )
        expected_checksum = manifest.get(f"{kind}_sha256")
        content = resolved.read_bytes()
        if b"\r\n" in content:
            content = content.replace(b"\r\n", b"\n")
        actual_checksum = hashlib.sha256(content).hexdigest()
        if expected_checksum != actual_checksum:
            raise ResourceManifestError(
                f"CV_{kind.upper()}_CHECKSUM_MISMATCH",
                f"CV {kind} resource checksum does not match the resource manifest",
            )
    taxonomy_file = (root / manifest["taxonomy_relative_path"]).resolve()
    from src.skill_taxonomy import (
        load_skill_taxonomy_snapshot,
        taxonomy_snapshot_version,
    )

    actual_taxonomy_version = taxonomy_snapshot_version(
        load_skill_taxonomy_snapshot(taxonomy_file)
    )
    if manifest.get("taxonomy_version") != actual_taxonomy_version:
        raise ResourceManifestError(
            "CV_TAXONOMY_VERSION_MISMATCH",
            "CV taxonomy version does not match the taxonomy snapshot",
        )
    return manifest
