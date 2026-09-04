"""Preflight or execute the controlled position-taxonomy.v3 P2 cutover."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

from jobgraph_contracts.position_catalog_v3 import ResolvedPositionCatalogV3


ROOT = Path(__file__).resolve().parents[1]


def _report(path: Path, schema: str) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != schema:
        raise ValueError(f"unexpected migration report schema: {path}")
    if payload.get("catalog_version") != "position-taxonomy.v3.0.0":
        raise ValueError(f"migration report does not use position taxonomy v3: {path}")
    return payload


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("cutover timestamps must be timezone-aware")
    return parsed


def _validate_existing_catalog(path: Path) -> ResolvedPositionCatalogV3:
    try:
        return ResolvedPositionCatalogV3.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise ValueError(f"existing position catalog is not reusable: {path}") from exc


def _bind_cutover_manifest(
    path: Path,
    *,
    catalog: ResolvedPositionCatalogV3,
    migration_run_id: str,
    release_id: str,
    window_start: str,
    window_end: str,
    git_commit: str,
    mode: str,
    parent_release_id: str | None,
) -> None:
    payload = {
        "schema_version": "position-v3-cutover-manifest.v1",
        "taxonomy_version": catalog.taxonomy_version,
        "migration_run_id": migration_run_id,
        "release_id": release_id,
        "window_start": _timestamp(window_start).isoformat(),
        "window_end": _timestamp(window_end).isoformat(),
        "git_commit": git_commit,
        "release_mode": mode,
        "parent_release_id": parent_release_id,
        "position_catalog_count": catalog.position_count,
        "position_catalog_codes": sorted(
            item.position_code for item in catalog.positions
        ),
    }
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != payload:
            raise ValueError(f"existing cutover manifest does not match: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _validate_existing_release(
    path: Path,
    *,
    release_id: str,
    window_start: str,
    window_end: str,
    git_commit: str,
    mode: str,
    parent_release_id: str | None,
) -> None:
    manifest_path = path / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"existing KG release has no manifest: {path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    window = manifest.get("observation_window")
    producer = manifest.get("producer")
    expected = {
        "release_schema_version": "kg-release-manifest.v1",
        "release_id": release_id,
        "mode": mode,
        "parent_release_id": parent_release_id,
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ValueError(f"existing KG release identity does not match cutover: {path}")
    if not isinstance(window, dict) or not isinstance(producer, dict):
        raise ValueError(f"existing KG release manifest is incomplete: {path}")
    if (
        _timestamp(str(window.get("start"))) != _timestamp(window_start)
        or _timestamp(str(window.get("end"))) != _timestamp(window_end)
        or producer.get("git_commit") != git_commit
    ):
        raise ValueError(f"existing KG release parameters do not match cutover: {path}")


def _run(command: list[str], *, execute: bool) -> None:
    print(json.dumps({"command": command, "execute": execute}, ensure_ascii=False))
    if execute:
        subprocess.run(command, cwd=ROOT, check=True)


def _post_reindex(url: str, *, execute: bool) -> None:
    print(json.dumps({"matching_reindex": url, "execute": execute}))
    if execute:
        request = urllib.request.Request(url, data=b"{}", method="POST")
        request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=120) as response:
            if response.status >= 300:
                raise RuntimeError(f"Matching reindex failed: HTTP {response.status}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jd-report", required=True, type=Path)
    parser.add_argument("--cv-report", required=True, type=Path)
    parser.add_argument("--jd-run-dir", action="append", required=True, type=Path)
    parser.add_argument("--cv-run-dir", action="append", required=True, type=Path)
    parser.add_argument("--cv-workbook", action="append", required=True, type=Path)
    parser.add_argument("--position-catalog-output", required=True, type=Path)
    parser.add_argument("--cutover-manifest-output", type=Path)
    parser.add_argument("--kg-release-output", required=True, type=Path)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--migration-run-id")
    parser.add_argument("--publisher-id", required=True)
    parser.add_argument("--validation-max-tasks", type=int)
    parser.add_argument("--window-start", required=True)
    parser.add_argument("--window-end", required=True)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--release-mode", choices=("full", "incremental"), default="full")
    parser.add_argument("--parent-release-id")
    parser.add_argument(
        "--matching-reindex-url",
        default="http://127.0.0.1:8010/internal/vector-index/reindex",
    )
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    jd_report = _report(args.jd_report, "position-reclassification-report.v3")
    cv_report = _report(args.cv_report, "cv-position-reclassification-report.v3")
    if len(args.cv_run_dir) != len(args.cv_workbook):
        raise ValueError("--cv-run-dir and --cv-workbook counts must match")
    reuse_catalog = args.position_catalog_output.exists()
    existing_catalog = (
        _validate_existing_catalog(args.position_catalog_output)
        if reuse_catalog
        else None
    )
    reuse_release = args.kg_release_output.exists()
    if reuse_release:
        _validate_existing_release(
            args.kg_release_output,
            release_id=args.release_id,
            window_start=args.window_start,
            window_end=args.window_end,
            git_commit=args.git_commit,
            mode=args.release_mode,
            parent_release_id=args.parent_release_id,
        )
    migration_run_id = args.migration_run_id or args.release_id
    cutover_manifest_output = args.cutover_manifest_output or (
        args.position_catalog_output.with_name(
            f"{args.position_catalog_output.stem}.cutover-manifest.json"
        )
    )
    validation_max_tasks = args.validation_max_tasks or (int(jd_report["document_count"]) + 10)

    _run(
        [
            sys.executable,
            "scripts/apply_position_v3_to_existing_jds.py",
            *sum((["--run-dir", str(path)] for path in args.jd_run_dir), []),
            "--migration-run-id",
            migration_run_id,
            *(["--execute"] if args.execute else []),
        ],
        execute=True,
    )
    _run(
        [
            sys.executable,
            "scripts/import_precomputed_cv_results.py",
            *sum((["--run-dir", str(path)] for path in args.cv_run_dir), []),
            *sum((["--workbook", str(path)] for path in args.cv_workbook), []),
            *(["--execute"] if args.execute else []),
        ],
        execute=True,
    )
    _run(
        [
            sys.executable,
            "scripts/run_pending_validation_tasks.py",
            "--max-tasks",
            str(validation_max_tasks),
        ],
        execute=args.execute,
    )
    _run(
        [
            sys.executable,
            "scripts/publish_position_v3_migrated_jds.py",
            "--migration-run-id",
            migration_run_id,
            "--publisher-id",
            args.publisher_id,
            "--expected-count",
            str(jd_report["document_count"]),
            *(["--execute"] if args.execute else []),
        ],
        execute=args.execute,
    )
    _run(
        [
            sys.executable,
            "scripts/export_resolved_position_catalog.py",
            str(args.position_catalog_output),
            *(["--verify-existing"] if reuse_catalog else []),
        ],
        execute=args.execute,
    )
    if args.execute:
        catalog = existing_catalog or _validate_existing_catalog(
            args.position_catalog_output
        )
        _bind_cutover_manifest(
            cutover_manifest_output,
            catalog=catalog,
            migration_run_id=migration_run_id,
            release_id=args.release_id,
            window_start=args.window_start,
            window_end=args.window_end,
            git_commit=args.git_commit,
            mode=args.release_mode,
            parent_release_id=args.parent_release_id,
        )
    _run(
        [
            sys.executable,
            "services/knowledge-graph/scripts/import_resolved_position_catalog.py",
            str(args.position_catalog_output),
        ],
        execute=args.execute,
    )
    export_command = [
        sys.executable,
        "scripts/export_kg_release.py",
        str(args.kg_release_output),
        "--release-id",
        args.release_id,
        "--window-start",
        args.window_start,
        "--window-end",
        args.window_end,
        "--git-commit",
        args.git_commit,
        "--mode",
        args.release_mode,
    ]
    if args.parent_release_id:
        export_command.extend(["--parent-release-id", args.parent_release_id])
    if not reuse_release:
        _run(
            export_command,
            execute=args.execute,
        )
    _run(
        [
            sys.executable,
            "services/knowledge-graph/scripts/import_release.py",
            str(args.kg_release_output),
        ],
        execute=args.execute,
    )
    _run(
        [
            sys.executable,
            "services/knowledge-graph/scripts/build_kg_graphs.py",
            "--workers",
            "1",
        ],
        execute=args.execute,
    )
    _post_reindex(args.matching_reindex_url, execute=args.execute)
    print(
        json.dumps(
            {
                "status": "executed" if args.execute else "preflight_passed",
                "jd_documents": jd_report["document_count"],
                "cv_roles": cv_report["role_count"],
                "taxonomy_version": "position-taxonomy.v3.0.0",
                "cutover_manifest": str(cutover_manifest_output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
