"""Export or import a manifest-verified Responsibility CE model package."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tarfile
from pathlib import Path, PurePosixPath
from uuid import uuid4

PACKAGE_FORMAT = "jobpulse-responsibility-ce.v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_and_verify_model(model_dir: Path) -> dict:
    manifest_path = model_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "responsibility-ce-artifact.v1":
        raise ValueError("unsupported Responsibility CE manifest")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Responsibility CE manifest has no files")
    for entry in entries:
        relative = _safe_relative(str(entry.get("path", "")))
        candidate = model_dir.joinpath(*relative.parts)
        if not candidate.is_file():
            raise ValueError(f"model file is missing: {relative.as_posix()}")
        if candidate.stat().st_size != int(entry["size"]):
            raise ValueError(f"model file size mismatch: {relative.as_posix()}")
        if _sha256(candidate) != str(entry["sha256"]).lower():
            raise ValueError(f"model file digest mismatch: {relative.as_posix()}")
    return manifest


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"unsafe model package path: {value}")
    return path


def export_package(model_dir: Path, output: Path) -> None:
    model_dir = model_dir.resolve()
    manifest = _load_and_verify_model(model_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w") as bundle:
        bundle.add(model_dir / "manifest.json", arcname="model/manifest.json")
        for entry in manifest["files"]:
            relative = _safe_relative(entry["path"])
            bundle.add(
                model_dir.joinpath(*relative.parts),
                arcname=f"model/{relative.as_posix()}",
            )
        package_manifest = {
            "package_format": PACKAGE_FORMAT,
            "artifact_sha256": manifest["artifact_sha256"],
            "model_id": manifest["model_id"],
            "model_revision": manifest["model_revision"],
        }
        payload = json.dumps(package_manifest, ensure_ascii=False, indent=2).encode("utf-8")
        info = tarfile.TarInfo("package-manifest.json")
        info.size = len(payload)
        bundle.addfile(info, __import__("io").BytesIO(payload))
    print(f"exported Responsibility CE package to {output}")


def import_package(package: Path, target: Path) -> None:
    package = package.resolve()
    target = target.resolve()
    if target.exists():
        existing = _load_and_verify_model(target)
        with tarfile.open(package, "r") as bundle:
            package_manifest = json.load(bundle.extractfile("package-manifest.json"))
        if existing["artifact_sha256"] == package_manifest.get("artifact_sha256"):
            print(f"Responsibility CE model already installed and verified at {target}")
            return
        raise ValueError(f"target contains a different Responsibility CE artifact: {target}")

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}.import-{uuid4().hex}"
    staging.mkdir(parents=False, exist_ok=False)
    try:
        with tarfile.open(package, "r") as bundle:
            package_manifest_file = bundle.extractfile("package-manifest.json")
            if package_manifest_file is None:
                raise ValueError("package is missing package-manifest.json")
            package_manifest = json.load(package_manifest_file)
            if package_manifest.get("package_format") != PACKAGE_FORMAT:
                raise ValueError("unsupported Responsibility CE package format")
            for member in bundle.getmembers():
                if member.name == "package-manifest.json":
                    continue
                relative = _safe_relative(member.name)
                if relative.parts[0] != "model" or not member.isfile():
                    raise ValueError(f"unexpected package member: {member.name}")
                source = bundle.extractfile(member)
                if source is None:
                    raise ValueError(f"cannot read package member: {member.name}")
                destination = staging.joinpath(*relative.parts[1:])
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("wb") as handle:
                    shutil.copyfileobj(source, handle, 4 * 1024 * 1024)
        installed = _load_and_verify_model(staging)
        if installed["artifact_sha256"] != package_manifest.get("artifact_sha256"):
            raise ValueError("package artifact digest does not match the embedded model manifest")
        os.replace(staging, target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    print(f"installed Responsibility CE model at {target}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("--model-dir", type=Path, required=True)
    export_parser.add_argument("--output", type=Path, required=True)
    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("--package", type=Path, required=True)
    import_parser.add_argument("--target", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "export":
        export_package(args.model_dir, args.output)
    else:
        import_package(args.package, args.target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
