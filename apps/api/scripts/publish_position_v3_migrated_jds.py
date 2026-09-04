"""Publish reviewed and validated historical position-taxonomy.v3 JD versions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.bootstrap.container import _build_application_container  # noqa: E402
from app.contexts.jd_lifecycle import Actor  # noqa: E402
from app.contracts.jd.normalization_v2 import JobClassification  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.core.database import create_database  # noqa: E402
from app.models.jd import JobDescription  # noqa: E402
from app.models.jd_parse_result import JDParseResult  # noqa: E402
from app.models.jd_publication import JDPublication  # noqa: E402
from app.models.review_task import ReviewTask  # noqa: E402


def publish_migration(
    *,
    settings: Settings,
    migration_run_id: str,
    publisher_id: str,
    expected_count: int,
    execute: bool,
) -> dict[str, int]:
    if settings.DATA_VALIDATION_MODE != "enforce":
        raise ValueError("historical v3 publication requires DATA_VALIDATION_MODE=enforce")
    database = create_database(settings.DATABASE_URL)
    published = already_published = 0
    try:
        with database.session_factory() as session:
            rows = list(session.scalars(select(JDParseResult)))
            migrated = [
                row
                for row in rows
                if isinstance(row.execution_metadata, dict)
                and row.execution_metadata.get("position_v3_migration_run_id") == migration_run_id
            ]
            if len(migrated) != expected_count:
                raise ValueError(
                    f"migration version count mismatch: {len(migrated)} != {expected_count}"
                )
            ready_ids: list[str] = []
            source_jd_ids: dict[str, str] = {}
            published_source_jd_ids: list[str] = []
            for parsed in migrated:
                classification = JobClassification.model_validate(
                    (parsed.normalized_result or {}).get("job_classification")
                )
                if classification.classification_status not in {
                    "resolved",
                    "manually_confirmed",
                }:
                    raise ValueError(f"migration classification is unresolved: {parsed.id}")
                publication = session.scalar(
                    select(JDPublication).where(JDPublication.parse_result_id == parsed.id)
                )
                if publication is not None:
                    already_published += 1
                    published_source_jd_ids.append(
                        str(parsed.execution_metadata["position_v3_source_jd_id"])
                    )
                    continue
                review = session.scalar(
                    select(ReviewTask).where(
                        ReviewTask.object_type == "jd_parse_result",
                        ReviewTask.object_id == parsed.id,
                    )
                )
                if (
                    review is None
                    or review.status != "approved"
                    or parsed.workflow_status != "reviewed"
                    or parsed.need_review
                ):
                    raise ValueError(f"migration JD still requires human review: {parsed.id}")
                ready_ids.append(parsed.id)
                source_jd_ids[parsed.id] = str(
                    parsed.execution_metadata["position_v3_source_jd_id"]
                )
        if execute:
            container = _build_application_container(settings, database)
            actor = Actor(publisher_id, "admin")
            for source_jd_id in published_source_jd_ids:
                with database.session_factory() as session:
                    source_jd = session.get(JobDescription, source_jd_id)
                    if source_jd is None:
                        raise ValueError("historical source JD no longer exists")
                    source_jd.is_deprecated = True
                    session.commit()
            for parse_result_id in ready_ids:
                container.jds.publish_parse_result_by_id(actor, parse_result_id)
                with database.session_factory() as session:
                    source_jd = session.get(JobDescription, source_jd_ids[parse_result_id])
                    if source_jd is None:
                        raise ValueError("historical source JD no longer exists")
                    source_jd.is_deprecated = True
                    session.commit()
                published += 1
        return {
            "migration_versions": len(migrated),
            "ready_to_publish": len(ready_ids),
            "published": published,
            "already_published": already_published,
            "executed": int(execute),
        }
    finally:
        database.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--migration-run-id", required=True)
    parser.add_argument("--publisher-id", required=True)
    parser.add_argument("--expected-count", required=True, type=int)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            publish_migration(
                settings=Settings(),
                migration_run_id=args.migration_run_id,
                publisher_id=args.publisher_id,
                expected_count=args.expected_count,
                execute=args.execute,
            ),
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
