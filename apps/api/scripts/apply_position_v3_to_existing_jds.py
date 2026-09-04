"""Bind independent JD position-taxonomy.v3 runs to existing main-system JDs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.contracts.jd.normalization_v2 import JobClassification  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import create_database  # noqa: E402
from app.models.jd import JobDescription  # noqa: E402
from app.models.jd_parse_result import JDParseResult  # noqa: E402
from app.models.jd_publication import JDPublication  # noqa: E402
from app.models.review_task import ReviewTask  # noqa: E402
from app.models.standard_position import StandardPosition  # noqa: E402
from app.infrastructure.jd_repository import SqlAlchemyJDUoW  # noqa: E402


def _classifications(run_dirs: list[Path]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for run_dir in run_dirs:
        path = run_dir / "final" / "normalized_annotations.json"
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise ValueError(f"normalized annotations must be a list: {path}")
        for row in rows:
            document_id = str(row.get("document_id") or "").strip()
            classification = row.get("job_classification")
            if not document_id or document_id in result or not isinstance(classification, dict):
                raise ValueError(f"invalid or duplicate Extraction document: {document_id}")
            validated = JobClassification.model_validate(classification)
            if validated.taxonomy_version != "position-taxonomy.v3.0.0":
                raise ValueError(f"JD classification is not v3: {document_id}")
            result[document_id] = validated.model_dump(mode="json")
    return result


def apply(
    run_dirs: list[Path],
    database_url: str,
    *,
    migration_run_id: str,
    execute: bool,
) -> dict[str, int]:
    if not migration_run_id.strip():
        raise ValueError("migration_run_id must be non-empty")
    classifications = _classifications(run_dirs)
    database = create_database(database_url)
    matched = created = existing = unresolved = 0
    validation_parse_result_ids: list[str] = []
    resumed_validation = 0
    try:
        with database.session_factory() as session:
            catalog = {
                str(row.position_code): row
                for row in session.scalars(
                    select(StandardPosition).where(
                        StandardPosition.taxonomy_version == "position-taxonomy.v3.0.0"
                    )
                )
                if row.position_code
            }
            rows = list(
                session.execute(
                    select(JDParseResult, JobDescription, JDPublication)
                    .join(JobDescription, JobDescription.id == JDParseResult.jd_id)
                    .outerjoin(
                        JDPublication,
                        JDPublication.parse_result_id == JDParseResult.id,
                    )
                ).all()
            )
            migrated_by_source_jd_id = {
                str(metadata.get("position_v3_source_jd_id")): parsed
                for parsed, _, _ in rows
                if isinstance((metadata := parsed.execution_metadata), dict)
                and metadata.get("position_v3_migration_run_id") == migration_run_id
                and metadata.get("position_v3_source_jd_id")
            }
            selected: dict[str, tuple[JDParseResult, JobDescription]] = {}
            for parsed, jd, publication in rows:
                source_document_id = str(jd.source_name or "")
                if source_document_id.startswith("batch:"):
                    source_document_id = source_document_id.removeprefix("batch:")
                if source_document_id not in classifications:
                    continue
                if isinstance(parsed.execution_metadata, dict) and parsed.execution_metadata.get(
                    "position_v3_migration_run_id"
                ):
                    continue
                current = selected.get(source_document_id)
                if current is None or publication is not None:
                    selected[source_document_id] = (parsed, jd)

            for source_document_id, classification_source in classifications.items():
                pair = selected.get(source_document_id)
                if pair is None:
                    continue
                parsed, jd = pair
                matched += 1
                migrated = migrated_by_source_jd_id.get(jd.id)
                if migrated is not None:
                    existing += 1
                    validation_parse_result_ids.append(migrated.id)
                    resumed_validation += 1
                    continue
                classification = dict(classification_source)
                status = classification["classification_status"]
                position_code = classification.get("position_code")
                bound = catalog.get(str(position_code)) if position_code else None
                if status in {"resolved", "manually_confirmed"}:
                    if bound is None or bound.lifecycle_status != "active":
                        raise ValueError(
                            f"classification has no active main binding: {source_document_id}"
                        )
                    classification["position_id"] = bound.id
                else:
                    classification["position_id"] = None
                    unresolved += 1
                normalized = dict(parsed.normalized_result or {})
                normalized["job_classification"] = classification
                new_jd_id = str(uuid4())
                new_parse_result_id = str(uuid4())
                new_jd = JobDescription(
                    id=new_jd_id,
                    source_type=jd.source_type,
                    source_name=jd.source_name,
                    enterprise_id=jd.enterprise_id,
                    title=jd.title,
                    raw_text=jd.raw_text,
                    cleaned_text=jd.cleaned_text,
                    publish_date=jd.publish_date,
                    url=jd.url,
                    file_id=jd.file_id,
                    source_document_id=jd.source_document_id,
                    extraction_bundle_version=jd.extraction_bundle_version,
                    parse_status="completed",
                    input_extraction_status=jd.input_extraction_status,
                    input_provider=jd.input_provider,
                    input_error_code=None,
                    input_error_message=None,
                    copy_risk_score=jd.copy_risk_score,
                    inflation_score=jd.inflation_score,
                    is_downweighted=jd.is_downweighted,
                )
                new_parse_result = JDParseResult(
                    id=new_parse_result_id,
                    jd_id=new_jd_id,
                    position_title=parsed.position_title,
                    responsibilities=list(parsed.responsibilities or []),
                    required_skills=list(parsed.required_skills or []),
                    bonus_skills=list(parsed.bonus_skills or []),
                    education=parsed.education,
                    experience=parsed.experience,
                    industry=parsed.industry,
                    tools=list(parsed.tools or []),
                    business_scenarios=list(parsed.business_scenarios or []),
                    parse_confidence=parsed.parse_confidence,
                    need_review=True,
                    extraction_result=dict(parsed.extraction_result or {}),
                    normalized_result=normalized,
                    execution_metadata={
                        "position_v3_migration_run_id": migration_run_id,
                        "position_v3_source_jd_id": jd.id,
                        "position_v3_source_parse_result_id": parsed.id,
                    },
                    schema_version=parsed.schema_version,
                    normalization_schema_version=parsed.normalization_schema_version,
                    workflow_status="draft",
                )
                session.add_all(
                    [
                        new_jd,
                        new_parse_result,
                        ReviewTask(
                            object_type="jd_parse_result",
                            object_id=new_parse_result_id,
                            priority="high",
                            reason=(
                                "position-taxonomy.v3 historical reclassification "
                                "requires human confirmation"
                            ),
                            status="pending",
                        ),
                    ]
                )
                validation_parse_result_ids.append(new_parse_result_id)
                created += 1
            expected = set(classifications)
            matched_ids = set(selected)
            missing = sorted(expected - matched_ids)
            if missing:
                raise ValueError(
                    f"v3 run contains documents absent from main system: {missing[:5]}"
                )
            if execute:
                session.commit()
            else:
                session.rollback()
        if execute:
            for parse_result_id in validation_parse_result_ids:
                with SqlAlchemyJDUoW(
                    database.session_factory,
                    data_validation_mode="enforce",
                ) as uow:
                    uow.stage_validation_for_parse_result(parse_result_id)
                    uow.commit()
    finally:
        database.dispose()
    return {
        "run_documents": len(classifications),
        "matched_main_jds": matched,
        "created_v3_jd_versions": created,
        "existing_v3_jd_versions": existing,
        "staged_validation_tasks": len(validation_parse_result_ids) if execute else 0,
        "resumed_validation_tasks": resumed_validation if execute else 0,
        "unresolved_classifications": unresolved,
        "executed": int(execute),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", action="append", required=True, type=Path)
    parser.add_argument("--database-url", default=settings.DATABASE_URL)
    parser.add_argument("--migration-run-id", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            apply(
                args.run_dir,
                args.database_url,
                migration_run_id=args.migration_run_id,
                execute=args.execute,
            ),
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
