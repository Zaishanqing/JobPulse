"""Immutable release package manifest shared by offline producers and consumers."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, model_validator

from jobgraph_contracts.base import StrictContract


class ReleaseMode(str, Enum):
    FULL = "full"
    INCREMENTAL = "incremental"


class ReleaseProducer(StrictContract):
    application: str = Field(min_length=1, max_length=128)
    git_commit: str = Field(min_length=1, max_length=128)


class ObservationWindow(StrictContract):
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def validate_order(self) -> "ObservationWindow":
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("observation window timestamps must be timezone-aware")
        if self.start > self.end:
            raise ValueError("observation window start must not exceed end")
        return self


class ReleaseArtifactV1(StrictContract):
    artifact_type: Literal["published-jd-facts"]
    contract_version: Literal["published-jd-fact.v3"]
    path: str = Field(min_length=1, max_length=240)
    record_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_path(self) -> "ReleaseArtifactV1":
        path = PurePosixPath(self.path)
        if path.is_absolute() or ".." in path.parts or self.path != path.as_posix():
            raise ValueError("artifact path must be a canonical relative POSIX path")
        if not self.path.endswith(".jsonl.gz"):
            raise ValueError("published JD fact artifacts must be .jsonl.gz")
        return self


class ReleaseManifestV1(StrictContract):
    release_schema_version: Literal["kg-release-manifest.v1"]
    release_id: str = Field(min_length=1, max_length=128)
    created_at: datetime
    producer: ReleaseProducer
    mode: ReleaseMode
    parent_release_id: str | None = Field(default=None, max_length=128)
    observation_window: ObservationWindow
    artifacts: list[ReleaseArtifactV1] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_release(self) -> "ReleaseManifestV1":
        if self.mode is ReleaseMode.FULL and self.parent_release_id is not None:
            raise ValueError("full release cannot declare parent_release_id")
        if self.parent_release_id == self.release_id:
            raise ValueError("release cannot be its own parent")
        paths = [artifact.path for artifact in self.artifacts]
        if len(paths) != len(set(paths)):
            raise ValueError("release artifact paths must be unique")
        return self
