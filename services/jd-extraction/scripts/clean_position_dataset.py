from __future__ import annotations

import argparse
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.position_cleaning import (  # noqa: E402
    clean_position_dataset,
    discover_run_dirs,
)


def _current_main_document_ids() -> set[str]:
    sql = (
        "select substring(jd.source_name from 7) "
        "from jd_parse_results jpr "
        "join job_descriptions jd on jd.id=jpr.jd_id "
        "join source_jds sj on sj.id=jd.source_jd_id "
        "order by 1;"
    )
    result = subprocess.run(
        [
            "docker",
            "exec",
            "jobgraph-integrated-main-postgres-1",
            "psql",
            "-U",
            "jobgraph_main",
            "-d",
            "jobgraph_main",
            "-Atc",
            sql,
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return {
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip()
    }


def _document_ids_from_package(package_path: Path) -> set[str]:
    with zipfile.ZipFile(package_path) as archive:
        matching = [
            name
            for name in archive.namelist()
            if name.endswith("/audit/current_document_ids.json")
        ]
        if len(matching) != 1:
            raise ValueError(
                "current ID package must contain exactly one "
                "audit/current_document_ids.json"
            )
        return {
            str(value)
            for value in json.loads(archive.read(matching[0]))
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Clean existing position-taxonomy.v3 classifications without "
            "calling an external model or overwriting source outputs."
        )
    )
    parser.add_argument(
        "--consolidated-run",
        type=Path,
        default=ROOT
        / "output"
        / "cleaned_bundles_all_unique_v3_20260812"
        / "bundles_all_unique_v3",
    )
    parser.add_argument(
        "--split-runs-root",
        type=Path,
        default=ROOT / "output" / "cleaned_runs_position_v3_20260812",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=ROOT / "config" / "position_taxonomy_catalog.v3.json",
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=ROOT / "config" / "position_cleaning_policy.v1.json",
    )
    parser.add_argument(
        "--reviewed-package",
        type=Path,
        help=(
            "Use a reviewed extraction audit ZIP as the authoritative cleaned "
            "source instead of local run directories."
        ),
    )
    parser.add_argument(
        "--current-ids-package",
        type=Path,
        help=(
            "Read the selected current document IDs from an existing cleaned "
            "position package."
        ),
    )
    parser.add_argument(
        "--classification-baseline-package",
        type=Path,
        help=(
            "Preserve already confirmed classifications from an existing "
            "cleaned position package while using the reviewed package for "
            "the authoritative extracted content."
        ),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--publish-only",
        action="store_true",
        help=(
            "Write only publishable records and aggregate reports; omit "
            "quarantine and per-record cleaning decisions from the package."
        ),
    )
    parser.add_argument(
        "--current-main-only",
        action="store_true",
        help="Restrict output to document IDs currently present in the main database.",
    )
    args = parser.parse_args()

    if args.current_ids_package:
        current_ids = _document_ids_from_package(args.current_ids_package)
    elif args.current_main_only:
        current_ids = _current_main_document_ids()
    else:
        current_ids = None
    report = clean_position_dataset(
        run_dirs=(
            None
            if args.reviewed_package
            else discover_run_dirs(
                args.consolidated_run,
                args.split_runs_root,
            )
        ),
        catalog_path=args.catalog,
        policy_path=args.policy,
        output_dir=args.output_dir,
        current_document_ids=current_ids,
        reviewed_package_path=args.reviewed_package,
        classification_baseline_path=args.classification_baseline_package,
        publish_only=args.publish_only,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
