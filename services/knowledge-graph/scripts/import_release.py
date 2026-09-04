"""Verify and atomically import a KG release package."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.fact_mappers import published_jd_fact
from app.application.contracts import ImportPublishedJDFactCommand, ImportReleaseCommand
from app.application.lineage_mapper import map_published_fact_lineage
from app.application.use_cases import ImportReleaseUseCase
from app.config import Settings
from app.database import create_database
from app.infrastructure.release_package import load_release_package
from app.infrastructure.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("--database-url")
    args = parser.parse_args()
    package = load_release_package(args.package)
    commands = []
    for body in package.facts:
        validation = body.validation_lineage.model_dump(mode="json")
        state = validation.pop("state")
        validation.pop("absent_reason")
        commands.append(
            ImportPublishedJDFactCommand(
                published_jd_fact(body),
                map_published_fact_lineage(
                    validation=validation if state == "present" else None,
                    catalog=body.skill_catalog_snapshot.model_dump(mode="json"),
                ),
            )
        )
    settings = Settings(
        **({"database_url": args.database_url} if args.database_url else {})
    )
    database = create_database(settings)
    use_case = ImportReleaseUseCase(
        lambda: SqlAlchemyUnitOfWork(database.session_factory)
    )
    result = use_case.execute(
        ImportReleaseCommand(
            package.manifest, package.manifest_hash, tuple(commands)
        )
    )
    print(
        f"release_id={result.release_id} record_count={result.record_count} "
        f"idempotent={str(result.idempotent).lower()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
