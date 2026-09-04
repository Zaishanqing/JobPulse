from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.targeted_run_repair import (  # noqa: E402
    apply_targeted_replacements,
    reextract_selected_documents,
    sanitize_targeted_extraction_audits,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply bounded, auditable repairs to selected JD records.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--selection", required=True)
    common.add_argument("--output-dir", default="output")
    common.add_argument("--data-root", default="data")
    common.add_argument("--normalization", default="config/normalization_map.yaml")
    subparsers.add_parser("replace", parents=[common])
    extract = subparsers.add_parser("extract", parents=[common])
    extract.add_argument("--model", default="deepseek-v4-flash")
    extract.add_argument("--semantic-retry-attempts", type=int, default=2)
    sanitize = subparsers.add_parser("sanitize-audits")
    sanitize.add_argument("--selection", required=True)
    sanitize.add_argument("--output-dir", default="output")
    args = parser.parse_args()

    if args.command == "replace":
        result = apply_targeted_replacements(
            args.selection, args.output_dir, args.data_root, args.normalization
        )
    elif args.command == "extract":
        result = reextract_selected_documents(
            args.selection,
            args.output_dir,
            args.data_root,
            args.normalization,
            args.model,
            args.semantic_retry_attempts,
        )
    else:
        result = sanitize_targeted_extraction_audits(args.selection, args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
