"""Read and fully verify a KG release directory before database mutation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path

from jobgraph_contracts.published_jd import PublishedJDFactV3
from jobgraph_contracts.release_manifest import ReleaseManifestV1


@dataclass(frozen=True)
class VerifiedReleasePackage:
    manifest: ReleaseManifestV1
    manifest_hash: str
    facts: tuple[PublishedJDFactV3, ...]


def load_release_package(root: Path) -> VerifiedReleasePackage:
    package_root = root.resolve(strict=True)
    if not package_root.is_dir():
        raise ValueError("release package path must be a directory")
    manifest_path = package_root / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest_json = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("release manifest is not valid UTF-8 JSON") from exc
    manifest = ReleaseManifestV1.model_validate(manifest_json)
    facts: list[PublishedJDFactV3] = []
    identities: set[tuple[str, str, str]] = set()
    for artifact in manifest.artifacts:
        artifact_path = (package_root / Path(*artifact.path.split("/"))).resolve()
        if package_root not in artifact_path.parents:
            raise ValueError("release artifact escapes package directory")
        compressed = artifact_path.read_bytes()
        try:
            content = gzip.decompress(compressed).decode("utf-8")
        except (gzip.BadGzipFile, UnicodeDecodeError) as exc:
            raise ValueError(f"release artifact is not UTF-8 gzip: {artifact.path}") from exc
        artifact_facts: list[PublishedJDFactV3] = []
        for line_number, line in enumerate(content.splitlines(), start=1):
            if not line.strip():
                raise ValueError(
                    f"release artifact contains a blank record at line {line_number}"
                )
            try:
                fact = PublishedJDFactV3.model_validate_json(line)
            except ValueError as exc:
                raise ValueError(
                    f"invalid published JD fact at {artifact.path}:{line_number}"
                ) from exc
            identity = (
                fact.source_system,
                fact.source_fact_id,
                fact.source_fact_version,
            )
            if identity in identities:
                raise ValueError("release contains a duplicate published fact version")
            identities.add(identity)
            published_at = datetime.fromisoformat(
                fact.published_at.replace("Z", "+00:00")
            )
            if not (
                manifest.observation_window.start
                <= published_at
                <= manifest.observation_window.end
            ):
                raise ValueError("published fact falls outside release observation window")
            artifact_facts.append(fact)
        if len(artifact_facts) != artifact.record_count:
            raise ValueError(f"release artifact record_count mismatch: {artifact.path}")
        facts.extend(artifact_facts)
    return VerifiedReleasePackage(
        manifest,
        hashlib.sha256(manifest_bytes).hexdigest(),
        tuple(facts),
    )
