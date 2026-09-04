from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.resource_manifest import (
    MANIFEST_SCHEMA,
    ResourceManifestError,
    validate_resource_manifest,
)


RESOURCES = Path(__file__).resolve().parents[1] / "resources"


def _manifest(tmp_path: Path) -> dict:
    root = tmp_path / "resources"
    shutil.copytree(RESOURCES, root)
    return root


def test_manifest_validates_checksums_and_versions(tmp_path):
    root = _manifest(tmp_path)
    result = validate_resource_manifest(
        root / "cv-resource-manifest.v1.json",
        normalization_path=root / "normalization" / "2.0" / "normalization_map.yaml",
        taxonomy_path=root / "taxonomy" / "2.0" / "skill_taxonomy_snapshot.json",
        normalization_version="2.0",
        cv_schema_version="2.4",
    )
    assert result["manifest_schema_version"] == MANIFEST_SCHEMA


@pytest.mark.parametrize("kind", ["normalization", "taxonomy"])
def test_manifest_rejects_checksum_mismatch(tmp_path, kind):
    root = _manifest(tmp_path)
    manifest = json.loads(
        (root / "cv-resource-manifest.v1.json").read_text(encoding="utf-8")
    )
    manifest[f"{kind}_sha256"] = "0" * 64
    (root / "cv-resource-manifest.v1.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    with pytest.raises(ResourceManifestError) as exc:
        validate_resource_manifest(
            root / "cv-resource-manifest.v1.json",
            normalization_path=root / "normalization" / "2.0" / "normalization_map.yaml",
            taxonomy_path=root / "taxonomy" / "2.0" / "skill_taxonomy_snapshot.json",
            normalization_version="2.0",
            cv_schema_version="2.4",
        )
    expected = (
        "CV_NORMALIZATION_CHECKSUM_MISMATCH"
        if kind == "normalization"
        else "CV_TAXONOMY_CHECKSUM_MISMATCH"
    )
    assert exc.value.code == expected


def test_manifest_rejects_taxonomy_version_mismatch(tmp_path):
    root = _manifest(tmp_path)
    manifest = json.loads(
        (root / "cv-resource-manifest.v1.json").read_text(encoding="utf-8")
    )
    manifest["taxonomy_version"] = "skill-taxonomy-snapshot.v2"
    (root / "cv-resource-manifest.v1.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    with pytest.raises(ResourceManifestError) as exc:
        validate_resource_manifest(
            root / "cv-resource-manifest.v1.json",
            normalization_path=root / "normalization" / "2.0" / "normalization_map.yaml",
            taxonomy_path=root / "taxonomy" / "2.0" / "skill_taxonomy_snapshot.json",
            normalization_version="2.0",
            cv_schema_version="2.4",
        )
    assert exc.value.code == "CV_TAXONOMY_VERSION_MISMATCH"


def test_manifest_rejects_normalization_version_mismatch(tmp_path):
    root = _manifest(tmp_path)
    with pytest.raises(ResourceManifestError) as exc:
        validate_resource_manifest(
            root / "cv-resource-manifest.v1.json",
            normalization_path=root / "normalization" / "2.0" / "normalization_map.yaml",
            taxonomy_path=root / "taxonomy" / "2.0" / "skill_taxonomy_snapshot.json",
            normalization_version="9.9",
            cv_schema_version="2.4",
        )
    assert exc.value.code == "CV_NORMALIZATION_RESOURCE_MISSING"
