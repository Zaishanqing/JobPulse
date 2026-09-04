"""Strict contracts shared by offline JD bundle producers and consumers."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from jobgraph_contracts.base import StrictContract


BUNDLE_SCHEMA_VERSION = "nfbs-jd-bundle.v1"
RECORD_SCHEMA_VERSION = "crawler-jd-v1"
BUNDLE_DATA_FILE = "jobs.jsonl.gz"
BUNDLE_SHA256SUMS_FILE = "SHA256SUMS"
# Formal transport bundle contains exactly these three members.
# Legacy two-member bundles are still accepted for compatibility.
BUNDLE_FILES = frozenset(
    {"manifest.json", BUNDLE_DATA_FILE, BUNDLE_SHA256SUMS_FILE}
)
BUNDLE_FILES_LEGACY = frozenset({"manifest.json", BUNDLE_DATA_FILE})


class BundleMode(str, Enum):
    INCREMENTAL = "incremental"
    FULL = "full"


class BundleProducer(StrictContract):
    application: str = Field(min_length=1, max_length=128)
    git_commit: str = Field(min_length=1, max_length=128)


class CrawlTimeRange(StrictContract):
    minimum: datetime | None = None
    maximum: datetime | None = None

    @model_validator(mode="after")
    def validate_order(self) -> "CrawlTimeRange":
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("crawl_time_range minimum must not exceed maximum")
        return self


class BundleManifestV1(StrictContract):
    bundle_schema_version: Literal["nfbs-jd-bundle.v1"] = BUNDLE_SCHEMA_VERSION
    bundle_id: str = Field(min_length=1, max_length=128)
    created_at: datetime
    producer: BundleProducer
    record_schema_version: Literal["crawler-jd-v1"] = RECORD_SCHEMA_VERSION
    mode: BundleMode
    parent_bundle_id: str | None = Field(default=None, max_length=128)
    record_count: int = Field(ge=0)
    crawl_time_range: CrawlTimeRange
    data_file: Literal["jobs.jsonl.gz"] = BUNDLE_DATA_FILE
    compressed_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    uncompressed_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_parent(self) -> "BundleManifestV1":
        if self.mode is BundleMode.FULL and self.parent_bundle_id is not None:
            raise ValueError("full bundles must not declare parent_bundle_id")
        if self.parent_bundle_id == self.bundle_id:
            raise ValueError("bundle cannot be its own parent")
        return self
