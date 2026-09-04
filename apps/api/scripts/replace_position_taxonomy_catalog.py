from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import text


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.core.database import create_database  # noqa: E402
from scripts.sync_position_taxonomy_catalog import DEFAULT_CATALOG, sync  # noqa: E402


CONFIRMATION = "DELETE_LEGACY_POSITION_PROJECTIONS"


def replace(catalog: Path, database_url: str, confirmation: str) -> dict[str, int]:
    if confirmation != CONFIRMATION:
        raise ValueError(f"--confirm must equal {CONFIRMATION}")
    database = create_database(database_url)
    try:
        with database.session_factory() as session:
            trend_reports = session.execute(text("SELECT COUNT(*) FROM trend_reports")).scalar_one()
            graph_versions = session.execute(text("SELECT COUNT(*) FROM graph_versions")).scalar_one()
            old_positions = session.execute(text("SELECT COUNT(*) FROM standard_positions")).scalar_one()
            session.execute(text("DELETE FROM trend_reports"))
            session.execute(text("DELETE FROM graph_versions"))
            session.execute(text("DELETE FROM standard_positions"))
            session.execute(text("UPDATE enterprise_jobs SET standard_position_id = NULL"))
            session.commit()
    finally:
        database.dispose()
    result = sync(catalog, database_url, check=False)
    return {
        "deleted_trend_reports": int(trend_reports),
        "deleted_graph_versions": int(graph_versions),
        "deleted_standard_positions": int(old_positions),
        **result,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Explicitly delete legacy position projections and install taxonomy v2."
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--database-url", default=settings.DATABASE_URL)
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    print(json.dumps(replace(args.catalog, args.database_url, args.confirm)))


if __name__ == "__main__":
    main()
