from __future__ import annotations

import gzip
import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1
from jobgraph_contracts.offline_bundle import (
    BUNDLE_SHA256SUMS_FILE,
    BundleManifestV1,
    BundleMode,
    BundleProducer,
    CrawlTimeRange,
)


def envelope(
    record_id: str, raw_text: str, *, source_version: str = "1"
) -> CrawlerJDEnvelopeV1:
    return CrawlerJDEnvelopeV1(
        source_record_id=record_id,
        source_platform="offline_test",
        source_version=source_version,
        source_url=f"https://example.test/{record_id}",
        job_title_raw=f"Job {record_id}",
        company_name_raw="Acme",
        region_raw="Local",
        crawl_time=datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc),
        raw_text=raw_text,
        raw_payload={"record_id": record_id},
        text_canonicalization_version="v1",
    )


def make_bundle(
    path: Path,
    *,
    bundle_id: str,
    envelopes: list[CrawlerJDEnvelopeV1] | None = None,
    raw_lines: list[bytes] | None = None,
    mode: BundleMode = BundleMode.FULL,
    parent_bundle_id: str | None = None,
    record_count: int | None = None,
    compressed_override: bytes | None = None,
) -> Path:
    values = envelopes or []
    lines = raw_lines or [
        json.dumps(
            value.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        for value in values
    ]
    uncompressed = b"".join(line + b"\n" for line in lines)
    compressed = (
        compressed_override
        if compressed_override is not None
        else gzip.compress(uncompressed, mtime=0)
    )

    crawl_times = [value.crawl_time for value in values]
    compressed_sha256 = hashlib.sha256(compressed).hexdigest()
    try:
        uncompressed_sha256 = hashlib.sha256(gzip.decompress(compressed)).hexdigest()
    except OSError:
        uncompressed_sha256 = None
    manifest = BundleManifestV1(
        bundle_id=bundle_id,
        created_at=datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc),
        producer=BundleProducer(application="test", git_commit="test"),
        mode=mode,
        parent_bundle_id=parent_bundle_id,
        record_count=len(lines) if record_count is None else record_count,
        crawl_time_range=CrawlTimeRange(
            minimum=min(crawl_times) if crawl_times else None,
            maximum=max(crawl_times) if crawl_times else None,
        ),
        compressed_sha256=compressed_sha256,
        uncompressed_sha256=uncompressed_sha256,
    )
    manifest_bytes = (
        json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    sha256sums = (
        f"{compressed_sha256}  jobs.jsonl.gz\n"
        f"{hashlib.sha256(manifest_bytes).hexdigest()}  manifest.json\n"
    ).encode("utf-8")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", manifest_bytes)
        archive.writestr("jobs.jsonl.gz", compressed)
        archive.writestr(BUNDLE_SHA256SUMS_FILE, sha256sums)
    return path
