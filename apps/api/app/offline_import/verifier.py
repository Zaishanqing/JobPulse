from __future__ import annotations

import gzip
import hashlib
from pathlib import Path

from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1
from jobgraph_contracts.offline_bundle import (
    BUNDLE_DATA_FILE,
    BUNDLE_SHA256SUMS_FILE,
    BundleManifestV1,
)

from app.offline_import.contracts import (
    BundleVerificationError,
    VerifiedBundle,
    VerifiedEnvelope,
)
from app.offline_import.reader import read_bundle_members


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_sha256sums(members: dict[str, bytes]) -> None:
    if BUNDLE_SHA256SUMS_FILE not in members:
        return
    try:
        checksum_text = members[BUNDLE_SHA256SUMS_FILE].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BundleVerificationError("SHA256SUMS is not valid UTF-8") from exc
    expected: dict[str, str] = {}
    for line_number, raw_line in enumerate(checksum_text.splitlines(), start=1):
        if not raw_line.strip():
            continue
        parts = raw_line.split(None, 1)
        if len(parts) != 2:
            raise BundleVerificationError(
                f"SHA256SUMS line {line_number} must be '<sha256>  <filename>'"
            )
        digest, filename = parts
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise BundleVerificationError(
                f"SHA256SUMS line {line_number} has an invalid sha256 digest"
            )
        expected[filename] = digest
    if set(expected) != {BUNDLE_DATA_FILE, "manifest.json"}:
        raise BundleVerificationError(
            "SHA256SUMS must list exactly manifest.json and jobs.jsonl.gz"
        )
    for filename, digest in expected.items():
        actual = _sha256_hex(members[filename])
        if actual != digest:
            raise BundleVerificationError(
                f"SHA256SUMS mismatch for {filename}: expected {digest}, got {actual}"
            )


def verify_bundle(path: Path) -> VerifiedBundle:
    path = path.resolve()
    members = read_bundle_members(path)
    _validate_sha256sums(members)
    manifest_bytes = members["manifest.json"]
    compressed = members["jobs.jsonl.gz"]
    try:
        manifest = BundleManifestV1.model_validate_json(manifest_bytes)
    except Exception as exc:
        raise BundleVerificationError(f"Invalid manifest: {exc}") from exc
    if manifest.compressed_sha256 is not None:
        actual = _sha256_hex(compressed)
        if actual != manifest.compressed_sha256:
            raise BundleVerificationError(
                "manifest compressed_sha256 does not match jobs.jsonl.gz"
            )
    try:
        uncompressed = gzip.decompress(compressed)
    except OSError as exc:
        raise BundleVerificationError("jobs.jsonl.gz gzip stream is invalid") from exc
    if manifest.uncompressed_sha256 is not None:
        actual = _sha256_hex(uncompressed)
        if actual != manifest.uncompressed_sha256:
            raise BundleVerificationError(
                "manifest uncompressed_sha256 does not match decompressed jobs.jsonl.gz"
            )
    raw_lines = uncompressed.splitlines()
    if len(raw_lines) != manifest.record_count:
        raise BundleVerificationError("record_count does not match JSONL lines")
    records = []
    for line_number, raw_line in enumerate(raw_lines, start=1):
        try:
            envelope = CrawlerJDEnvelopeV1.model_validate_json(raw_line)
        except Exception as exc:
            raise BundleVerificationError(f"Invalid Envelope at line {line_number}: {exc}") from exc
        records.append(VerifiedEnvelope(line_number, envelope))
    bundle_digest = _sha256_hex(
        b"manifest.json\0" + manifest_bytes + b"jobs.jsonl.gz\0" + compressed
    )
    return VerifiedBundle(
        path=path,
        manifest=manifest,
        records=tuple(records),
        bundle_digest=bundle_digest,
    )
