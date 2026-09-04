from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.run_renormalizer import renormalize_run  # noqa: E402


DEFAULT_NORMALIZATION = (
    PROJECT_ROOT / "resources" / "normalization" / "2.0" / "normalization_map.yaml"
)
DEFAULT_SKILL_TAXONOMY = (
    PROJECT_ROOT / "resources" / "taxonomy" / "2.0" / "skill_taxonomy_snapshot.json"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Re-normalize an existing CV run with the JD taxonomy and build MatchFeature artifacts."
    )
    parser.add_argument("--run-dir", required=True, help="Existing CV run directory.")
    parser.add_argument(
        "--normalization",
        default=str(DEFAULT_NORMALIZATION),
        help="Authoritative JD normalization YAML.",
    )
    parser.add_argument(
        "--skill-taxonomy-snapshot",
        default=str(DEFAULT_SKILL_TAXONOMY),
        help="Reviewed main-catalog skill taxonomy snapshot.",
    )
    parser.add_argument(
        "--as-of-date",
        type=date.fromisoformat,
        default=date.today(),
        help="Date used to close ongoing work intervals, in YYYY-MM-DD format.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = renormalize_run(
        args.run_dir,
        args.normalization,
        as_of_date=args.as_of_date,
        skill_taxonomy_path=args.skill_taxonomy_snapshot,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
