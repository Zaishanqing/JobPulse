from __future__ import annotations

import gzip
import hashlib
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1
from jobgraph_contracts.offline_bundle import (
    BUNDLE_DATA_FILE,
    BUNDLE_FILES,
    BUNDLE_FILES_LEGACY,
    BUNDLE_SHA256SUMS_FILE,
    BundleManifestV1,
)


class BundleVerificationError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedExportBundle:
    manifest: BundleManifestV1
    records: tuple[CrawlerJDEnvelopeV1, ...]


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


def verify_bundle(path: Path) -> VerifiedExportBundle:
    members: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(path) as archive:
            files = [item for item in archive.infolist() if not item.is_dir()]
            names = {item.filename for item in files}
            if names not in (BUNDLE_FILES, BUNDLE_FILES_LEGACY) or len(files) != len(names):
                raise BundleVerificationError(
                    f"Bundle files must be exactly {sorted(BUNDLE_FILES)} "
                    f"or legacy {sorted(BUNDLE_FILES_LEGACY)}"
                )
            members = {item.filename: archive.read(item) for item in files}
    except BundleVerificationError:
        raise
    except (OSError, zipfile.BadZipFile, KeyError, UnicodeError) as exc:
        raise BundleVerificationError(f"Unreadable bundle: {exc}") from exc

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
        raise BundleVerificationError("jobs.jsonl.gz is invalid") from exc
    if manifest.uncompressed_sha256 is not None:
        actual = _sha256_hex(uncompressed)
        if actual != manifest.uncompressed_sha256:
            raise BundleVerificationError(
                "manifest uncompressed_sha256 does not match decompressed jobs.jsonl.gz"
            )
    lines = uncompressed.splitlines()
    if len(lines) != manifest.record_count:
        raise BundleVerificationError("record_count does not match JSONL lines")
    records = []
    for line_number, line in enumerate(lines, start=1):
        try:
            records.append(CrawlerJDEnvelopeV1.model_validate_json(line))
        except Exception as exc:
            raise BundleVerificationError(
                f"Invalid Envelope at line {line_number}: {exc}"
            ) from exc
    return VerifiedExportBundle(manifest, tuple(records))


def inspect_bundle(path: Path) -> dict[str, object]:
    return json.loads(verify_bundle(path).manifest.model_dump_json())
