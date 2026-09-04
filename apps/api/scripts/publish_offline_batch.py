from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.bootstrap.container import _build_application_container  # noqa: E402
from app.contexts.jd_lifecycle import Actor  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.core.database import create_database  # noqa: E402
from app.domain.accounts import AccountActor  # noqa: E402
from app.contracts.jd.normalization_v2 import JobClassification  # noqa: E402
from app.models.data_validation import (  # noqa: E402
    DataValidationTask,
    ValidatedBundleSnapshot,
    ValidationReport,
)
from app.models.extraction_task import ExtractionTask  # noqa: E402
from app.models.offline_import import OfflineImportBatch, OfflineImportItem  # noqa: E402
from app.models.review_task import ReviewTask  # noqa: E402


@dataclass(frozen=True)
class PublishBatchSummary:
    bundle_id: str
    selected_count: int
    excluded_blocked_count: int
    excluded_unresolved_classification_count: int
    imported_draft_count: int
    approved_review_count: int
    published_count: int
    standard_position_count: int


def _position_bindings(database, container, task_ids: list[str]):
    classifications: dict[str, str] = {}
    with database.session_factory() as session:
        for task_id in task_ids:
            task = session.get(ExtractionTask, task_id)
            raw = task.bundle_payload if task is not None else None
            classification = (
                raw.get("normalized_result", {}).get("job_classification")
                if isinstance(raw, dict)
                else None
            )
            if not isinstance(classification, dict):
                raise ValueError(f"JD classification is missing: {task_id}")
            validated = JobClassification.model_validate(classification)
            if validated.classification_status not in {
                "resolved",
                "manually_confirmed",
            }:
                raise ValueError(f"JD classification is not resolved: {task_id}")
            position_code = str(validated.position_code or "").strip()
            position_name = str(validated.position_name or "").strip()
            if not position_code or not position_name:
                raise ValueError(f"Resolved JD classification is incomplete: {task_id}")
            previous = classifications.setdefault(position_code, position_name)
            if previous != position_name:
                raise ValueError(f"Conflicting JD classification: {position_code}")
    positions = {
        item.position_code: item for item in container.positions.list() if item.position_code
    }
    for position_code, position_name in sorted(classifications.items()):
        position = positions.get(position_code)
        if position is None:
            raise ValueError(
                f"JD classification references missing standard position: {position_code}"
            )
        if (
            position.position_name != position_name
            or position.taxonomy_version != "position-taxonomy.v3.0.0"
            or position.lifecycle_status != "active"
        ):
            raise ValueError(f"JD classification conflicts with standard position: {position_code}")
    return {
        position_code: (positions[position_code].position_id, position_name)
        for position_code, position_name in classifications.items()
    }


def _positive_limit(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("limit must be positive")
    return parsed


def _selected_task_ids(database, bundle_id: str, limit: int | None) -> list[str]:
    with database.session_factory() as session:
        batch = session.scalar(
            select(OfflineImportBatch).where(OfflineImportBatch.bundle_id == bundle_id)
        )
        if batch is None:
            raise ValueError(f"Offline import batch does not exist: {bundle_id}")
        if batch.status != "completed":
            raise ValueError(f"Offline import batch is not completed: {batch.status}")
        items = list(
            session.scalars(
                select(OfflineImportItem)
                .where(OfflineImportItem.batch_id == batch.id)
                .order_by(OfflineImportItem.line_number)
            )
        )
        if limit is not None:
            items = items[:limit]
        if not items:
            raise ValueError("Offline import batch contains no selected records")
        task_ids: list[str] = []
        for item in items:
            if item.status != "imported" or item.extraction_task_id is None:
                raise ValueError(
                    f"Offline import line {item.line_number} is not an imported extraction task"
                )
            task_ids.append(item.extraction_task_id)
        return task_ids


def _preflight(
    database,
    task_ids: list[str],
    *,
    exclude_blocked: bool,
) -> tuple[list[str], int, int]:
    ready: list[str] = []
    blocked_count = 0
    unresolved_classification_count = 0
    with database.session_factory() as session:
        for task_id in task_ids:
            task = session.get(ExtractionTask, task_id)
            if task is None or task.status != "succeeded":
                status = None if task is None else task.status
                raise ValueError(f"Extraction task is not succeeded: {task_id} ({status})")
            validation_task = session.scalar(
                select(DataValidationTask).where(
                    DataValidationTask.extraction_task_id == task_id,
                    DataValidationTask.status == "succeeded",
                )
            )
            if validation_task is None:
                raise ValueError(f"Validation task is not succeeded: {task_id}")
            report = session.scalar(
                select(ValidationReport).where(
                    ValidationReport.data_validation_task_id == validation_task.id
                )
            )
            if report is None:
                raise ValueError(f"Validation report is missing: {task_id}")
            if report.conclusion == "block":
                if not exclude_blocked:
                    raise ValueError(
                        "Blocking validation reports exist; rerun with "
                        "--exclude-blocked to publish only non-blocking records"
                    )
                blocked_count += 1
                continue
            classification = (
                (task.bundle_payload or {}).get("normalized_result", {}).get("job_classification")
            )
            try:
                validated = JobClassification.model_validate(classification)
            except (TypeError, ValueError):
                unresolved_classification_count += 1
                continue
            if validated.classification_status not in {
                "resolved",
                "manually_confirmed",
            }:
                unresolved_classification_count += 1
                continue
            snapshot = session.scalar(
                select(ValidatedBundleSnapshot).where(
                    ValidatedBundleSnapshot.extraction_task_id == task_id
                )
            )
            if snapshot is None:
                raise ValueError(f"Validated bundle snapshot is missing: {task_id}")
            ready.append(task_id)
    if not ready:
        raise ValueError("No class-resolved, non-blocking validated records were selected")
    return ready, blocked_count, unresolved_classification_count


def publish_offline_batch(
    *,
    settings: Settings,
    bundle_id: str,
    reviewer_id: str,
    publisher_id: str,
    limit: int | None = None,
    exclude_blocked: bool = False,
) -> PublishBatchSummary:
    if settings.DATA_VALIDATION_MODE != "enforce":
        raise ValueError("publish_offline_batch requires DATA_VALIDATION_MODE=enforce")
    if not bundle_id.strip() or not reviewer_id.strip() or not publisher_id.strip():
        raise ValueError("bundle_id, reviewer_id and publisher_id are required")
    database = create_database(settings.DATABASE_URL)
    try:
        container = _build_application_container(settings, database)
        task_ids = _selected_task_ids(database, bundle_id, limit)
        task_ids, blocked_count, unresolved_classification_count = _preflight(
            database,
            task_ids,
            exclude_blocked=exclude_blocked,
        )
        reviewer = AccountActor(reviewer_id, "reviewer")
        publisher = Actor(publisher_id, "admin")
        position_bindings = _position_bindings(database, container, task_ids)
        imported = approved = published = 0
        for task_id in task_ids:
            with database.session_factory() as session:
                validation_task = session.scalar(
                    select(DataValidationTask).where(
                        DataValidationTask.extraction_task_id == task_id,
                        DataValidationTask.status == "succeeded",
                    )
                )
                report = (
                    session.scalar(
                        select(ValidationReport).where(
                            ValidationReport.data_validation_task_id == validation_task.id
                        )
                    )
                    if validation_task is not None
                    else None
                )
                validation_review = (
                    session.scalar(
                        select(ReviewTask).where(
                            ReviewTask.object_type == "data_validation_report",
                            ReviewTask.object_id == report.id,
                        )
                    )
                    if report is not None and report.conclusion == "warn"
                    else None
                )
            if report is not None and report.conclusion == "warn":
                if validation_review is None:
                    raise ValueError(f"Validation review task is missing: {report.id}")
                if validation_review.status == "pending":
                    container.governance.reviews.transition(
                        reviewer,
                        validation_review.id,
                        "approve",
                        "Approved reviewed Validation warning for real-data publication.",
                    )
                elif validation_review.status != "approved":
                    raise ValueError(
                        f"Validation review is not approved: {validation_review.id} "
                        f"({validation_review.status})"
                    )
            draft = container.extraction_tasks.import_extraction_bundle(
                task_id, position_bindings=position_bindings
            )
            imported += 1
            with database.session_factory() as session:
                review = session.scalar(
                    select(ReviewTask).where(
                        ReviewTask.object_type == "jd_parse_result",
                        ReviewTask.object_id == draft.parse_result_id,
                    )
                )
                if review is None:
                    raise ValueError(f"JD draft review task is missing: {draft.parse_result_id}")
                review_id = review.id
                review_status = review.status
            if review_status not in {"pending", "approved"}:
                raise ValueError(
                    f"JD draft review task is not actionable: {review_id} ({review_status})"
                )
            result = container.governance.reviews.transition(
                reviewer,
                review_id,
                "approve",
                "Approved reviewed Extraction result for real-data publication.",
            )
            if result.status != "approved":
                raise ValueError(f"JD draft review was not approved: {review_id}")
            approved += 1
            container.jds.publish_parse_result_by_id(
                publisher,
                draft.parse_result_id,
            )
            published += 1
        return PublishBatchSummary(
            bundle_id=bundle_id,
            selected_count=len(task_ids),
            excluded_blocked_count=blocked_count,
            excluded_unresolved_classification_count=(unresolved_classification_count),
            imported_draft_count=imported,
            approved_review_count=approved,
            published_count=published,
            standard_position_count=len(position_bindings),
        )
    finally:
        database.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Approve and publish a validated, non-blocking offline JD batch."
    )
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument("--reviewer-id", required=True)
    parser.add_argument("--publisher-id", required=True)
    parser.add_argument("--limit", type=_positive_limit)
    parser.add_argument(
        "--exclude-blocked",
        action="store_true",
        help="Explicitly exclude records whose Validation conclusion is block.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = publish_offline_batch(
        settings=Settings(),
        bundle_id=args.bundle_id,
        reviewer_id=args.reviewer_id,
        publisher_id=args.publisher_id,
        limit=args.limit,
        exclude_blocked=args.exclude_blocked,
    )
    print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
