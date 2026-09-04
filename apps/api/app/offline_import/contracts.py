from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1
from jobgraph_contracts.offline_bundle import BundleManifestV1


class BundleVerificationError(ValueError):
    pass


class BundleImportConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class VerifiedEnvelope:
    line_number: int
    envelope: CrawlerJDEnvelopeV1


@dataclass(frozen=True)
class VerifiedBundle:
    path: Path
    manifest: BundleManifestV1
    records: tuple[VerifiedEnvelope, ...]
    bundle_digest: str


@dataclass(frozen=True)
class ImportBatchRecord:
    batch_id: str
    bundle_id: str
    status: str
    bundle_digest: str | None


@dataclass(frozen=True)
class ImportSummary:
    batch_id: str
    bundle_id: str
    record_count: int
    imported_count: int
    skipped_count: int
    failed_count: int
    status: str
    no_op: bool = False
