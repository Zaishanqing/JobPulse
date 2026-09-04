#!/usr/bin/env python3
"""Re-export a legacy real Offline Bundle into the current strict format.

Historical ``nfbs-unified-crawler`` archives wrote ``sha256:<hex>`` into
``BundleManifestV1.compressed_sha256 / uncompressed_sha256`` while the current
contract requires bare 64-char hex.  This script does NOT touch any record:
``jobs.jsonl.gz`` stays byte-identical.  It verifies the legacy archive's
SHA256SUMS, strips the ``sha256:`` prefix from the two manifest hash fields,
rewrites ``manifest.json`` and ``SHA256SUMS``, and writes a provenance sidecar
so acceptance artifacts can be traced back to the original real archive.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import zipfile
from pathlib import Path


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_members(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        files = [item for item in archive.infolist() if not item.is_dir()]
        names = {item.filename for item in files}
        if names != {"manifest.json", "jobs.jsonl.gz", "SHA256SUMS"}:
            raise ValueError(
                f"legacy bundle must contain exactly manifest.json, jobs.jsonl.gz, "
                f"SHA256SUMS; got {sorted(names)}"
            )
        return {item.filename: archive.read(item) for item in files}


def _verify_legacy_sha256sums(members: dict[str, bytes]) -> None:
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
        actual = _sha256_hex(members[filename])
        if actual != digest:
            raise ValueError(f"SHA256SUMS mismatch for {filename}")


def _strip_hash_prefix(value: str, label: str) -> str:
    if value.startswith("sha256:") and len(value) == 71:
        return value[len("sha256:") :]
    raise ValueError(f"{label} must use the legacy 'sha256:<64 hex>' format")


def rebuild_legacy_bundle(path: Path, output: Path) -> dict[str, object]:
    members = _read_members(path)
    _verify_legacy_sha256sums(members)

    manifest = json.loads(members["manifest.json"].decode("utf-8"))
    legacy_compressed = str(manifest["compressed_sha256"])
    legacy_uncompressed = str(manifest["uncompressed_sha256"])
    compressed = _strip_hash_prefix(legacy_compressed, "compressed_sha256")
    uncompressed = _strip_hash_prefix(legacy_uncompressed, "uncompressed_sha256")

    actual_compressed = _sha256_hex(members["jobs.jsonl.gz"])
    if actual_compressed != compressed:
        raise ValueError("legacy manifest compressed_sha256 does not match jobs.jsonl.gz")
    actual_uncompressed = _sha256_hex(gzip.decompress(members["jobs.jsonl.gz"]))
    if actual_uncompressed != uncompressed:
        raise ValueError("legacy manifest uncompressed_sha256 does not match jobs.jsonl.gz")

    manifest["compressed_sha256"] = compressed
    manifest["uncompressed_sha256"] = uncompressed
    manifest_bytes = (
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    sha256sums = (
        f"{compressed}  jobs.jsonl.gz\n"
        f"{_sha256_hex(manifest_bytes)}  manifest.json\n"
    ).encode("utf-8")

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, mode="x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in (
            ("manifest.json", manifest_bytes),
            ("jobs.jsonl.gz", members["jobs.jsonl.gz"]),
            ("SHA256SUMS", sha256sums),
        ):
            archive.writestr(name, payload)

    provenance = {
        "schema": "B-ACQ-REPLAY.bundle-provenance.v1",
        "source_archive": str(path.resolve()),
        "source_archive_sha256": _sha256_hex(path.read_bytes()),
        "source_bundle_id": manifest["bundle_id"],
        "source_producer_application": manifest["producer"]["application"],
        "source_producer_git_commit": manifest["producer"]["git_commit"],
        "jobs_jsonl_gz_sha256": compressed,
        "transformation": (
            "legacy manifest hash fields normalized from 'sha256:<hex>' to bare hex; "
            "jobs.jsonl.gz byte-identical; SHA256SUMS regenerated for the new manifest"
        ),
        "output_archive": str(output.resolve()),
    }
    (output.with_name(output.name + ".provenance.json")).write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return provenance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Legacy real bundle zip")
    parser.add_argument("--output", type=Path, required=True, help="Output current-format zip")
    args = parser.parse_args(argv)
    provenance = rebuild_legacy_bundle(args.input, args.output)
    print(json.dumps(provenance, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
