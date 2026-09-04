from __future__ import annotations

import zipfile

import pytest

from app.offline_import import BundleVerificationError
from app.offline_import.verifier import verify_bundle
from tests.offline_bundle_test_support import (
    envelope,
    make_bundle,
)


def test_verifier_accepts_valid_bundle(tmp_path):
    path = make_bundle(
        tmp_path / "valid.zip",
        bundle_id="bundle-valid",
        envelopes=[envelope("one", "first"), envelope("two", "second")],
    )

    verified = verify_bundle(path)

    assert verified.manifest.bundle_id == "bundle-valid"
    assert [item.envelope.source_record_id for item in verified.records] == [
        "one",
        "two",
    ]


def test_verifier_rejects_bad_zip_path_traversal_and_missing_manifest(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(BundleVerificationError, match="ZIP"):
        verify_bundle(bad)

    traversal = tmp_path / "traversal.zip"
    with zipfile.ZipFile(traversal, "w") as archive:
        archive.writestr("../manifest.json", b"{}")
        archive.writestr("jobs.jsonl.gz", b"")
    with pytest.raises(BundleVerificationError, match="exactly"):
        verify_bundle(traversal)

    missing = tmp_path / "missing.zip"
    with zipfile.ZipFile(missing, "w") as archive:
        archive.writestr("jobs.jsonl.gz", b"")
    with pytest.raises(BundleVerificationError, match="exactly"):
        verify_bundle(missing)


def test_verifier_rejects_tampered_sha256sums(tmp_path):
    original = make_bundle(
        tmp_path / "valid-sums.zip",
        bundle_id="bundle-sums",
        envelopes=[envelope("one", "first")],
    )
    tampered = tmp_path / "tampered-sums.zip"
    with zipfile.ZipFile(original) as src:
        with zipfile.ZipFile(tampered, "w", compression=zipfile.ZIP_DEFLATED) as dst:
            for item in src.infolist():
                data = src.read(item.filename)
                if item.filename == "SHA256SUMS":
                    data = data.replace(b"jobs.jsonl.gz", b"jobs.jsonl.gz", 1)
                    # Corrupt the first digest character while preserving format.
                    lines = data.splitlines()
                    parts = lines[0].split(None, 1)
                    first = parts[0]
                    replacement = b"0" if first[0:1] != b"0" else b"1"
                    corrupted = replacement + first[1:]
                    lines[0] = corrupted + b"  " + parts[1]
                    data = b"\n".join(lines) + b"\n"
                dst.writestr(item, data)

    with pytest.raises(BundleVerificationError, match="SHA256SUMS"):
        verify_bundle(tampered)


def test_verifier_rejects_gzip_jsonl_and_count_damage(tmp_path):
    gzip_path = make_bundle(
        tmp_path / "gzip.zip",
        bundle_id="bundle-gzip",
        envelopes=[envelope("one", "first")],
        compressed_override=b"not-gzip",
    )
    with pytest.raises(BundleVerificationError, match="gzip"):
        verify_bundle(gzip_path)

    jsonl_path = make_bundle(
        tmp_path / "jsonl.zip",
        bundle_id="bundle-jsonl",
        raw_lines=[b"{broken-json"],
    )
    with pytest.raises(BundleVerificationError, match="line 1"):
        verify_bundle(jsonl_path)

    count_path = make_bundle(
        tmp_path / "count.zip",
        bundle_id="bundle-count",
        envelopes=[envelope("one", "first")],
        record_count=2,
    )
    with pytest.raises(BundleVerificationError, match="record_count"):
        verify_bundle(count_path)
