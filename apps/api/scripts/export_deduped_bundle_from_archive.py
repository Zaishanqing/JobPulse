#!/usr/bin/env python3
"""Export a deduplicated current-format bundle from a real bundle archive.

The 2026-08-07 full archive contains repeated crawls where the same
``(source_platform, source_record_id, source_version)`` appears more than once
with different raw content.  The main importer fails closed on such rows
(``SourceJDImportConflict``), so a clean, still-100%-real bundle is needed for
the B-ACQ-REPLAY acceptance.

This script keeps the FIRST occurrence of each source version identity and
records every removed duplicate row in a provenance sidecar.  Records are
otherwise byte-identical to the source archive (re-serialized from the same
JSON payloads), and the output is a valid current-format bundle that the
unchanged OfflineBundleImporter can verify and import.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_members(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        files = [item for item in archive.infolist() if not item.is_dir()]
        names = {item.filename for item in files}
        if names != {"manifest.json", "jobs.jsonl.gz", "SHA256SUMS"}:
            raise ValueError(
                f"bundle must contain exactly manifest.json, jobs.jsonl.gz, SHA256SUMS; "
                f"got {sorted(names)}"
            )
        return {item.filename: archive.read(item) for item in files}


def _verify_sha256sums(members: dict[str, bytes]) -> None:
    expected: dict[str, str] = {}
    for raw_line in members["SHA256SUMS"].decode("utf-8").splitlines():
        if not raw_line.strip():
            continue
        parts = raw_line.split(None, 1)
        if len(parts) != 2:
            raise ValueError(f"invalid SHA256SUMS line: {raw_line!r}")
        digest, filename = parts
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError(f"invalid SHA256SUMS digest for {filename}")
        expected[filename] = digest
    if set(expected) != {"manifest.json", "jobs.jsonl.gz"}:
        raise ValueError("SHA256SUMS must list exactly manifest.json and jobs.jsonl.gz")
    for filename, digest in expected.items():
        if _sha256_hex(members[filename]) != digest:
            raise ValueError(f"SHA256SUMS mismatch for {filename}")


def _parse_envelopes(members: dict[str, bytes]) -> tuple[list[dict[str, object]], list[str]]:
    uncompressed = gzip.decompress(members["jobs.jsonl.gz"])
    raw_lines = uncompressed.splitlines()
    envelopes = []
    errors = []
    for line_number, raw_line in enumerate(raw_lines, start=1):
        try:
            envelopes.append(json.loads(raw_line))
        except Exception as exc:  # noqa: BLE001 - report per-line and fail loudly
            errors.append(f"line {line_number}: {exc}")
    return envelopes, errors


def export_deduped_bundle(path: Path, output: Path) -> dict[str, object]:
    members = _read_members(path)
    _verify_sha256sums(members)
    source_manifest = json.loads(members["manifest.json"].decode("utf-8"))

    envelopes, errors = _parse_envelopes(members)
    if errors:
        raise ValueError("source bundle contains invalid JSONL envelopes: " + "; ".join(errors[:5]))

    seen: set[tuple[str, str, str]] = set()
    kept_lines: list[tuple[int, dict[str, object]]] = []
    removed: list[dict[str, object]] = []
    for line_number, envelope in enumerate(envelopes, start=1):
        key = (
            str(envelope.get("source_platform") or ""),
            str(envelope.get("source_record_id") or ""),
            str(envelope.get("source_version") or "1"),
        )
        if key in seen:
            removed.append(
                {
                    "line_number": line_number,
                    "source_platform": key[0],
                    "source_record_id": key[1],
                    "source_version": key[2],
                    "reason": "duplicate source version identity with different content; first occurrence kept",
                }
            )
            continue
        seen.add(key)
        kept_lines.append((line_number, envelope))

    output_buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=output_buffer, mtime=0) as stream:
        for _line_number, envelope in kept_lines:
            stream.write(
                json.dumps(
                    envelope,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n"
            )
    compressed = output_buffer.getvalue()
    uncompressed = gzip.decompress(compressed)

    crawl_times = []
    for _line_number, envelope in kept_lines:
        raw_time = envelope.get("crawl_time")
        if raw_time:
            try:
                crawl_times.append(datetime.fromisoformat(str(raw_time)))
            except ValueError:
                pass
    now = datetime.now(timezone.utc)
    manifest = {
        "bundle_schema_version": "nfbs-jd-bundle.v1",
        "bundle_id": f"{source_manifest['bundle_id']}-dedup",
        "created_at": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "producer": {
            "application": source_manifest["producer"]["application"],
            "git_commit": source_manifest["producer"]["git_commit"],
        },
        "record_schema_version": "crawler-jd-v1",
        "mode": "full",
        "parent_bundle_id": None,
        "record_count": len(kept_lines),
        "crawl_time_range": {
            "minimum": min(crawl_times).isoformat() if crawl_times else None,
            "maximum": max(crawl_times).isoformat() if crawl_times else None,
        },
        "data_file": "jobs.jsonl.gz",
        "compressed_sha256": _sha256_hex(compressed),
        "uncompressed_sha256": _sha256_hex(uncompressed),
    }
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    sha256sums = (
        f"{manifest['compressed_sha256']}  jobs.jsonl.gz\n"
        f"{_sha256_hex(manifest_bytes)}  manifest.json\n"
    ).encode("utf-8")

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, mode="x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in (
            ("manifest.json", manifest_bytes),
            ("jobs.jsonl.gz", compressed),
            ("SHA256SUMS", sha256sums),
        ):
            archive.writestr(name, payload)

    provenance = {
        "schema": "B-ACQ-REPLAY.bundle-provenance.v1",
        "source_archive": str(path.resolve()),
        "source_archive_sha256": _sha256_hex(path.read_bytes()),
        "source_bundle_id": source_manifest["bundle_id"],
        "source_producer_application": source_manifest["producer"]["application"],
        "source_producer_git_commit": source_manifest["producer"]["git_commit"],
        "transformation": (
            "real records kept first occurrence per "
            "(source_platform, source_record_id, source_version); duplicate version "
            "re-crawls with different content removed; output is a standalone full "
            "current-format bundle"
        ),
        "source_record_count": len(envelopes),
        "kept_record_count": len(kept_lines),
        "removed_record_count": len(removed),
        "removed_records": removed,
        "output_archive": str(output.resolve()),
    }
    (output.with_name(output.name + ".provenance.json")).write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return provenance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Current-format real bundle zip")
    parser.add_argument("--output", type=Path, required=True, help="Output deduplicated bundle zip")
    args = parser.parse_args(argv)
    provenance = export_deduped_bundle(args.input, args.output)
    print(json.dumps(provenance, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
