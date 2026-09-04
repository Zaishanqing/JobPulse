"""Clean existing Extraction run outputs into importable run directories.

Each cleaned run keeps the original raw text, adds the canonical cleaned text,
remaps Evidence to the cleaned text, and removes platform watermark artifacts
from semantic values. The output layout is a run package that
``build_extraction_audit_package.py`` and the main-system full-import flow can
consume directly.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.run_cleaning import clean_runs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-dir",
        action="append",
        help="run directory to clean; repeatable",
    )
    parser.add_argument(
        "--runs-root",
        default=Path("output/runs"),
        type=Path,
        help="directory scanned when --run-dir is not provided",
    )
    parser.add_argument(
        "--run-id",
        action="append",
        default=[],
        help="only clean these run IDs under --runs-root; repeatable",
    )
    parser.add_argument(
        "--output-root",
        default=Path("output/cleaned_runs"),
        type=Path,
        help="root directory for cleaned run packages",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing cleaned run directory",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_dirs: list[Path] = []
    if args.run_dir:
        run_dirs = [Path(item).resolve() for item in args.run_dir]
    else:
        runs_root = Path(args.runs_root).resolve()
        selected = set(args.run_id)
        for candidate in sorted(runs_root.iterdir()):
            if candidate.is_dir() and candidate.name.startswith("."):
                continue
            if not selected or candidate.name in selected:
                run_dirs.append(candidate)
    summaries = clean_runs(run_dirs, Path(args.output_root).resolve(), force=args.force)
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
