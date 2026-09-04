from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.post_review import apply_post_review_file


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply strict, audited human post-review corrections.")
    parser.add_argument("--decisions", required=True, help="Version 1.0 post-review decision JSON.")
    parser.add_argument("--output-dir", default="output", help="Pipeline output directory.")
    parser.add_argument(
        "--normalization",
        default="config/normalization_map.yaml",
        help="Normalization YAML used to rebuild derived outputs.",
    )
    parser.add_argument(
        "--run-id",
        action="append",
        dest="run_ids",
        help="Apply only the selected run_id. Repeat to select multiple runs.",
    )
    args = parser.parse_args()
    receipts = apply_post_review_file(
        args.decisions,
        args.output_dir,
        args.normalization,
        selected_run_ids=set(args.run_ids) if args.run_ids else None,
    )
    print(json.dumps(receipts, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
