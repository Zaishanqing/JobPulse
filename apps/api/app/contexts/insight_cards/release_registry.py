from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

from jobgraph_contracts.published_jd import PublishedJDFactV3
from jobgraph_contracts.release_manifest import ReleaseManifestV1


@dataclass(frozen=True)
class ReleaseIdentity:
    release_id: str
    observation_window_start: datetime
    observation_window_end: datetime
    created_at: datetime
    producer_application: str
    producer_git_commit: str
    membership_keys: frozenset[tuple[str, str, str]]


class ReleaseNotFound(LookupError):
    pass


class ReleaseArtifactInvalid(ValueError):
    pass


class ReleaseRegistry(Protocol):
    def resolve(self, release_id: str) -> ReleaseIdentity: ...
    def evidence_belongs_to_release(
        self,
        identity: ReleaseIdentity,
        *,
        source_jd_id: str,
        source_fact_id: str,
        source_version: str,
    ) -> bool: ...


class ManifestReleaseRegistry:
    """Resolve immutable release identity from frozen kg-release manifests."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._cache: dict[str, ReleaseIdentity] = {}

    def resolve(self, release_id: str) -> ReleaseIdentity:
        cached = self._cache.get(release_id)
        if cached is not None:
            return cached
        manifest_path = self._base_dir / release_id / "manifest.json"
        if not manifest_path.is_file():
            raise ReleaseNotFound(release_id)
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = ReleaseManifestV1.model_validate(raw)
        if manifest.release_id != release_id:
            raise ReleaseNotFound(release_id)
        membership_keys = self._load_membership(manifest_path.parent, manifest)
        identity = ReleaseIdentity(
            release_id=manifest.release_id,
            observation_window_start=manifest.observation_window.start,
            observation_window_end=manifest.observation_window.end,
            created_at=manifest.created_at,
            producer_application=manifest.producer.application,
            producer_git_commit=manifest.producer.git_commit,
            membership_keys=membership_keys,
        )
        self._cache[release_id] = identity
        return identity

    def _load_membership(
        self,
        release_dir: Path,
        manifest: ReleaseManifestV1,
    ) -> frozenset[tuple[str, str, str]]:
        keys: set[tuple[str, str, str]] = set()
        for artifact in manifest.artifacts:
            artifact_path = release_dir / artifact.path
            if not artifact_path.is_file():
                raise ReleaseArtifactInvalid(
                    f"release artifact missing: {artifact.path}"
                )
            try:
                rows = list(_read_jsonl(artifact_path))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ReleaseArtifactInvalid(
                    f"release artifact unreadable: {artifact.path}"
                ) from exc
            if len(rows) != artifact.record_count:
                raise ReleaseArtifactInvalid(
                    f"release artifact record_count mismatch: {artifact.path}"
                )
            for row in rows:
                try:
                    fact = PublishedJDFactV3.model_validate(row)
                except ValueError as exc:
                    raise ReleaseArtifactInvalid(
                        f"invalid published JD fact: {artifact.path}"
                    ) from exc
                key = (
                    fact.source_jd_id,
                    fact.source_fact_id,
                    fact.source_fact_version,
                )
                if key in keys:
                    raise ReleaseArtifactInvalid(
                        f"duplicate release membership key: {key!r}"
                    )
                keys.add(key)
        return frozenset(keys)

    def evidence_belongs_to_release(
        self,
        identity: ReleaseIdentity,
        *,
        source_jd_id: str,
        source_fact_id: str,
        source_version: str,
    ) -> bool:
        return (source_jd_id, source_fact_id, source_version) in identity.membership_keys

    def evidence_within_release(
        self,
        identity: ReleaseIdentity,
        publish_date: date | None,
    ) -> bool:
        if publish_date is None:
            return False
        start = identity.observation_window_start.date()
        end = identity.observation_window_end.date()
        return start <= publish_date <= end


__all__ = [
    "ManifestReleaseRegistry",
    "ReleaseArtifactInvalid",
    "ReleaseIdentity",
    "ReleaseNotFound",
    "ReleaseRegistry",
]


def _read_jsonl(path: Path):
    opener = gzip.open if path.suffix == ".gz" else Path.open
    if path.suffix == ".gz":
        handle = opener(path, mode="rt", encoding="utf-8")
    else:
        handle = opener(path, mode="r", encoding="utf-8")
    with handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)
