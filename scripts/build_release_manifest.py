#!/usr/bin/env python3
"""Freeze the immutable inputs for a JobPulse production release."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_sha(root: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()


def migration_heads(root: Path) -> dict[str, list[str]]:
    locations = {
        "main": root / "apps/api/alembic/versions",
        "matching": root / "services/matching-service/migrations/versions",
    }
    return {
        name: sorted(path.name for path in directory.glob("*.py"))
        for name, directory in locations.items()
        if directory.is_dir()
    }


def compose_images(root: Path, compose_file: Path, env_file: Path | None) -> list[dict[str, Any]]:
    command = [
        "docker",
        "compose",
        "--project-directory",
        str(compose_file.parent.parent),
        "-f",
        str(compose_file),
    ]
    if env_file is not None:
        command.extend(["--env-file", str(env_file)])
    command.extend(["config", "--images"])
    names = subprocess.check_output(command, cwd=root, text=True).splitlines()
    images = []
    for image in sorted({name.strip() for name in names if name.strip()}):
        inspected = subprocess.check_output(
            ["docker", "image", "inspect", image], cwd=root, text=True
        )
        payload = json.loads(inspected)[0]
        images.append(
            {
                "image": image,
                "image_id": payload.get("Id"),
                "repo_digests": sorted(payload.get("RepoDigests") or []),
                "immutable": bool(payload.get("RepoDigests")),
            }
        )
    return images


def build_manifest(
    root: Path,
    *,
    compose_file: Path,
    env_file: Path | None,
    model_manifest: Path | None,
    sbom_files: list[Path],
) -> dict[str, Any]:
    config_files = [compose_file, root / "infra/.env.example"]
    config = [
        {"path": str(path.relative_to(root)).replace("\\", "/"), "sha256": sha256_file(path)}
        for path in config_files
        if path.is_file()
    ]
    model = None
    if model_manifest is not None:
        if not model_manifest.is_file():
            raise ValueError(f"model manifest does not exist: {model_manifest}")
        payload = json.loads(model_manifest.read_text(encoding="utf-8"))
        model = {
            "path": str(model_manifest.relative_to(root)).replace("\\", "/"),
            "manifest_sha256": sha256_file(model_manifest),
            "artifact_sha256": payload.get("artifact_sha256"),
        }
    sbom = [
        {"path": str(path.relative_to(root)).replace("\\", "/"), "sha256": sha256_file(path)}
        for path in sbom_files
        if path.is_file()
    ]
    images = compose_images(root, compose_file, env_file)
    complete = bool(images) and all(item["immutable"] for item in images) and bool(sbom)
    if model_manifest is not None:
        complete = complete and bool(model and model["artifact_sha256"])
    return {
        "schema_version": "jobpulse-release-manifest.v1",
        "release_status": "complete" if complete else "incomplete",
        "git_sha": git_sha(root),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "compose": config,
        "images": images,
        "migration_heads": migration_heads(root),
        "model": model,
        "sbom": sbom,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--compose-file", type=Path, default=Path("infra/compose/docker-compose.candidate.yml"))
    parser.add_argument("--env-file", type=Path, default=Path("infra/.env.example"))
    parser.add_argument("--model-manifest", type=Path)
    parser.add_argument("--sbom", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    compose_file = (root / args.compose_file).resolve()
    env_file = (root / args.env_file).resolve() if args.env_file else None
    model_manifest = (root / args.model_manifest).resolve() if args.model_manifest else None
    sbom_files = [(root / path).resolve() for path in args.sbom]
    manifest = build_manifest(
        root,
        compose_file=compose_file,
        env_file=env_file,
        model_manifest=model_manifest,
        sbom_files=sbom_files,
    )
    if args.require_complete and manifest["release_status"] != "complete":
        raise SystemExit("release manifest is incomplete: immutable images, SBOM, and model inputs are required")
    output = (root / args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"release_status={manifest['release_status']} git_sha={manifest['git_sha']} output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
