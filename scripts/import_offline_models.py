"""Verify and atomically install an offline BGE snapshot into a HF cache."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

PACKAGE_FORMAT = "jobgraph-embedding-models.v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--expected-revision", required=True)
    return parser.parse_args()


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"unsafe package path: {value}")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    args = parse_args()
    if len(args.expected_revision) != 40:
        raise SystemExit("expected revision must be a 40-character commit hash")
    with tarfile.open(args.package, "r") as bundle:
        try:
            manifest_member = bundle.getmember("manifest.json")
        except KeyError as exc:
            raise SystemExit("package is missing manifest.json") from exc
        manifest_file = bundle.extractfile(manifest_member)
        if manifest_file is None:
            raise SystemExit("manifest.json is not a regular file")
        manifest = json.load(manifest_file)
        if manifest.get("package_format") != PACKAGE_FORMAT:
            raise SystemExit("unsupported model package format")
        revision = str(manifest.get("revision", ""))
        if revision != args.expected_revision:
            raise SystemExit(
                f"package revision {revision} does not match {args.expected_revision}"
            )
        repo_id = str(manifest.get("repo_id", ""))
        entries = manifest.get("files")
        if not repo_id or not isinstance(entries, list) or not entries:
            raise SystemExit("model package manifest is incomplete")

        args.cache_dir.mkdir(parents=True, exist_ok=True)
        model_root = args.cache_dir / ("models--" + repo_id.replace("/", "--"))
        snapshot_target = model_root / "snapshots" / revision
        if snapshot_target.exists():
            for entry in entries:
                relative = _safe_relative(str(entry.get("path", "")))
                installed = snapshot_target.joinpath(*relative.parts)
                if (
                    not installed.is_file()
                    or installed.stat().st_size != int(entry["size_bytes"])
                    or _sha256(installed) != str(entry["sha256"]).lower()
                ):
                    raise SystemExit(
                        f"snapshot target exists but does not match the package: {snapshot_target}"
                    )
            print(f"model snapshot already installed and verified at {snapshot_target}")
            return 0
        snapshots_root = snapshot_target.parent
        snapshots_root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{revision}.import-", dir=snapshots_root))
        try:
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                    raise ValueError("manifest contains an invalid file entry")
                relative = _safe_relative(entry["path"])
                member_name = f"snapshot/{relative.as_posix()}"
                member = bundle.getmember(member_name)
                source = bundle.extractfile(member)
                if source is None or not member.isfile():
                    raise ValueError(f"package member is not a regular file: {member_name}")
                target = staging.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("wb") as handle:
                    shutil.copyfileobj(source, handle, 4 * 1024 * 1024)
                if target.stat().st_size != int(entry["size_bytes"]):
                    raise ValueError(f"size mismatch for {relative.as_posix()}")
                if _sha256(target) != str(entry["sha256"]).lower():
                    raise ValueError(f"SHA-256 mismatch for {relative.as_posix()}")
            os.replace(staging, snapshot_target)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    print(f"installed {repo_id} @ {revision} into {snapshot_target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
