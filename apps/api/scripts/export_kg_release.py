"""Export main-system JD publications as an immutable KG Release package."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.core.database import create_database
from app.integrations.knowledge_graph.release_export import export_kg_release


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must be timezone-aware")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--window-start", required=True, type=_timestamp)
    parser.add_argument("--window-end", required=True, type=_timestamp)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--mode", choices=("full", "incremental"), default="full")
    parser.add_argument("--parent-release-id")
    args = parser.parse_args()
    database = create_database(settings.DATABASE_URL)
    with database.session_factory() as session:
        manifest = export_kg_release(
            session,
            args.output,
            release_id=args.release_id,
            window_start=args.window_start,
            window_end=args.window_end,
            git_commit=args.git_commit,
            mode=args.mode,
            parent_release_id=args.parent_release_id,
        )
    database.dispose()
    print(
        f"release_id={manifest.release_id} "
        f"record_count={sum(item.record_count for item in manifest.artifacts)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
