from __future__ import annotations

import argparse
import json
from pathlib import Path

from jobgraph_contracts.offline_bundle import BundleMode

from unified_api.offline_export.exporter import (
    BundleExporter,
    validate_export_request,
)
from unified_api.offline_export.manifest import inspect_bundle, verify_bundle


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NFBS offline JD bundle tools")
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--output", type=Path, required=True)
    export.add_argument(
        "--mode", choices=[item.value for item in BundleMode], required=True
    )
    export.add_argument("--parent-bundle-id")
    export.add_argument(
        "--limit",
        type=int,
        help="Maximum records for incremental exports; invalid for full bundles.",
    )
    export.add_argument("--producer-git-commit", default="unknown")
    verify = commands.add_parser("verify")
    verify.add_argument("bundle", type=Path)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("bundle", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "export":
        mode = BundleMode(args.mode)
        validate_export_request(mode=mode, limit=args.limit)

        # Database imports stay inside the only command that needs crawler
        # MySQL. Help, inspect, and verify can therefore run on a bundle-only
        # computer without dbutils, database configuration, or a MySQL service.
        from unified_api.database import ensure_schema
        from unified_api.offline_export.repository import MySQLExportRepository

        ensure_schema()
        summary = BundleExporter(MySQLExportRepository()).export(
            output=args.output,
            mode=mode,
            parent_bundle_id=args.parent_bundle_id,
            limit=args.limit,
            producer_git_commit=args.producer_git_commit,
        )
        print(
            json.dumps(
                {
                    "bundle_id": summary.bundle_id,
                    "records": summary.record_count,
                    "file": str(summary.output_path),
                },
                ensure_ascii=False,
            )
        )
        return 0
    if args.command == "verify":
        verified = verify_bundle(args.bundle)
        print(f"bundle_id: {verified.manifest.bundle_id}")
        print(f"records: {len(verified.records)}")
        print("status: verified")
        return 0
    print(json.dumps(inspect_bundle(args.bundle), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
