from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
JOBPULSE_ROOT = PROJECT_ROOT.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.normalizer import load_normalization_map  # noqa: E402
from src.skill_taxonomy import (  # noqa: E402
    load_skill_taxonomy_snapshot,
    validate_snapshot_against_normalization_map,
)


DEFAULT_CATALOG = (
    JOBPULSE_ROOT
    / "apps"
    / "api"
    / "config"
    / "skill_taxonomy_catalog.v1.json"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and export the main-system reviewed skill taxonomy catalog. "
            "This command never infers classifications from legacy categories."
        )
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_CATALOG,
        help="Main-system reviewed catalog source.",
    )
    parser.add_argument(
        "--normalization",
        default="config/normalization_map.yaml",
        help="Normalization map used only for exact identity coverage validation.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "config" / "skill_taxonomy_snapshot.json",
        help="Extraction runtime snapshot output path.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    catalog = load_skill_taxonomy_snapshot(args.catalog)
    normalization_map = load_normalization_map(args.normalization)
    validate_snapshot_against_normalization_map(catalog, normalization_map)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.catalog, args.output)
    exported = load_skill_taxonomy_snapshot(args.output)
    validate_snapshot_against_normalization_map(exported, normalization_map)
    print(
        json.dumps(
            {
                "source": str(args.catalog.resolve()),
                "output": str(args.output.resolve()),
                "node_count": len(exported["nodes"]),
                "approved_skill_count": len(exported["skills"]),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
