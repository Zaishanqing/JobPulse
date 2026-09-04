"""Package a warmed Hugging Face snapshot into a verifiable offline model tar.

Runs inside a Linux container (see scripts/export-offline-models.ps1): the
container-created cache uses symlinks that Windows hosts cannot follow, so
packaging must happen where the symlinks resolve.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path

PACKAGE_FORMAT = "jobgraph-embedding-models.v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-id", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--dimension", type=int, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    args = parse_args()
    model_cache = args.cache_dir / ("models--" + args.repo_id.replace("/", "--"))
    snapshot_dir = model_cache / "snapshots" / args.revision
    if not snapshot_dir.is_dir():
        raise SystemExit(
            f"Model snapshot not found: {snapshot_dir}. "
            "Warm the cache first (start the stack once, or run scripts/prefetch_bge_m3.py)."
        )
    incomplete = sorted(model_cache.rglob("*.incomplete"))
    if incomplete:
        raise SystemExit(
            f"Cache contains an incomplete download: {incomplete[0]}. "
            "Finish the prefetch before exporting."
        )

    files = sorted(path for path in snapshot_dir.rglob("*") if path.is_file())
    if not files:
        raise SystemExit(f"Snapshot {snapshot_dir} is empty.")

    manifest_files = []
    for path in files:
        relative = path.relative_to(snapshot_dir).as_posix()
        manifest_files.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_of(path),
            }
        )
    manifest = {
        "package_format": PACKAGE_FORMAT,
        "repo_id": args.repo_id,
        "revision": args.revision,
        "dimension": args.dimension,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": manifest_files,
    }

    # Uncompressed tar: model weights do not compress, and a single seekable
    # file copies faster from USB drives than thousands of small ones.
    # dereference=True stores symlink targets as regular files so the package
    # can be unpacked on Windows hosts without symlink privileges.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(args.output, "w", dereference=True) as bundle:
        payload = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        info = tarfile.TarInfo("manifest.json")
        info.size = len(payload)
        bundle.addfile(info, fileobj=__import__("io").BytesIO(payload))
        for path, entry in zip(files, manifest_files):
            bundle.add(path, arcname=f"snapshot/{entry['path']}")

    size_mb = round(args.output.stat().st_size / (1024 * 1024))
    print(f"packed {len(manifest_files)} files into {args.output} ({size_mb} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
