from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path

from app.bootstrap.container import _build_application_container
from app.core.config import Settings
from app.core.database import create_database
from app.offline_import.importer import OfflineBundleImporter
from app.offline_import.repository import OfflineImportRepository
from app.offline_import.verifier import verify_bundle


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="local JD bundle importer")
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("bundle", type=Path)
    import_command = commands.add_parser("import")
    import_command.add_argument("bundle", type=Path)
    import_command.add_argument("--allow-gap", action="store_true")
    import_command.add_argument("--retry", action="store_true")
    commands.add_parser("history")
    show = commands.add_parser("show")
    show.add_argument("bundle_id")
    return parser


def _print_summary(values: dict[str, object]) -> None:
    for key in (
        "bundle_id",
        "record_count",
        "imported_count",
        "skipped_count",
        "failed_count",
        "status",
        "no_op",
    ):
        if key in values:
            print(f"{key}: {values[key]}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "verify":
        bundle = verify_bundle(args.bundle)
        print(f"bundle_id: {bundle.manifest.bundle_id}")
        print(f"records: {len(bundle.records)}")
        print("status: verified")
        return 0

    settings = Settings()
    database = create_database(settings.DATABASE_URL)
    try:
        repository = OfflineImportRepository(database.session_factory)
        if args.command == "history":
            for summary in repository.history():
                _print_summary(asdict(summary))
                print()
            return 0
        if args.command == "show":
            summary = repository.summary(args.bundle_id)
            if summary is None:
                print("Bundle import not found")
                return 1
            _print_summary(asdict(summary))
            return 0
        container = _build_application_container(settings, database)
        importer = OfflineBundleImporter(
            repository,
            container.extraction_tasks.import_crawler_envelope_as_jd,
            "rule",
        )
        summary = importer.import_bundle(
            args.bundle,
            allow_gap=args.allow_gap,
            retry=args.retry,
        )
        _print_summary(asdict(summary))
        return 0 if summary.failed_count == 0 else 2
    finally:
        database.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
