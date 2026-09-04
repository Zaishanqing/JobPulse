"""Fail unless the knowledge graph has at least one published position profile."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.database import create_database  # noqa: E402
from app.models import GraphVersion, StandardPosition  # noqa: E402


def main() -> int:
    # Optional diagnostic only: when set, the position's publish state is
    # reported (and a warning printed) but never decides the exit code.
    required_code = os.environ.get("KG_REQUIRED_POSITION_CODE", "").strip()

    settings = Settings.from_env()
    database = create_database(settings)
    result: dict[str, object]
    try:
        with database.session_factory() as session:
            position = None
            current_version = None
            if required_code:
                position = session.scalar(
                    select(StandardPosition).where(
                        StandardPosition.position_code == required_code,
                        StandardPosition.status == "active",
                    )
                )
                current_version = (
                    session.get(GraphVersion, position.current_version_id)
                    if position is not None and position.current_version_id is not None
                    else None
                )
            published_profiles = session.scalar(
                select(StandardPosition.position_id)
                .where(
                    StandardPosition.status == "active",
                    StandardPosition.current_version_id.is_not(None),
                )
                .limit(1)
            )
            result = {
                "database_schema_available": True,
                "required_position_code": required_code or None,
                "required_position_found": position is not None,
                "required_position_published": bool(
                    position is not None and current_version is not None
                ),
                "any_published_profile": published_profiles is not None,
            }
    except SQLAlchemyError as exc:
        result = {
            "database_schema_available": False,
            "required_position_code": required_code or None,
            "required_position_found": False,
            "required_position_published": False,
            "any_published_profile": False,
            "error": type(exc).__name__,
        }
    finally:
        database.engine.dispose()

    print(json.dumps(result, ensure_ascii=False))
    if not (result["database_schema_available"] and result["any_published_profile"]):
        print(
            "KG graph data is missing. Run scripts\\compose-init-kg.ps1 after "
            "the real published facts have been imported, then retry -Full.",
            file=sys.stderr,
        )
        return 1
    if required_code and not result["required_position_published"]:
        print(
            f"warning: position {required_code} has no published graph version; "
            "matching against it will fail until it is republished.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
