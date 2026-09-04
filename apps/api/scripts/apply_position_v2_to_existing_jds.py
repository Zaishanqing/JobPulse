from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.contracts.jd.normalization_v2 import JobClassification  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import create_database  # noqa: E402
from app.models.jd import JobDescription  # noqa: E402
from app.models.jd_parse_result import JDParseResult  # noqa: E402
from app.models.standard_position import StandardPosition  # noqa: E402


def _classifications(package: Path) -> dict[str, dict[str, object]]:
    with zipfile.ZipFile(package) as archive:
        rows = json.loads(
            archive.read("final/normalized_annotations.json").decode("utf-8")
        )
    if not isinstance(rows, list):
        raise ValueError("normalized_annotations.json must contain a list")
    result: dict[str, dict[str, object]] = {}
    for row in rows:
        document_id = str(row.get("document_id") or "").strip()
        classification = row.get("job_classification")
        if not document_id or document_id in result or not isinstance(classification, dict):
            raise ValueError(f"invalid or duplicate Extraction document: {document_id}")
        JobClassification.model_validate(classification)
        result[document_id] = classification
    return result


def apply(package: Path, database_url: str, *, check: bool) -> dict[str, int]:
    classifications = _classifications(package)
    database = create_database(database_url)
    matched = updated = 0
    try:
        with database.session_factory() as session:
            position_ids = set(session.scalars(select(StandardPosition.id)).all())
            rows = session.execute(
                select(JDParseResult, JobDescription)
                .join(JobDescription, JobDescription.id == JDParseResult.jd_id)
                .where(JobDescription.source_name.like("batch:%"))
            ).all()
            missing: list[str] = []
            for parsed, jd in rows:
                source_document_id = str(jd.source_name).removeprefix("batch:")
                classification = classifications.get(source_document_id)
                if classification is None:
                    missing.append(source_document_id)
                    continue
                if classification["position_id"] not in position_ids:
                    raise ValueError(
                        f"classification references missing standard position: {source_document_id}"
                    )
                matched += 1
                normalized = dict(parsed.normalized_result or {})
                if normalized.get("job_classification") != classification:
                    normalized["job_classification"] = classification
                    parsed.normalized_result = normalized
                    updated += 1
            if missing:
                raise ValueError(
                    f"main-system JD has no Extraction v2 classification: {missing[:5]}"
                )
            if check:
                session.rollback()
            else:
                session.commit()
    finally:
        database.dispose()
    return {
        "package_documents": len(classifications),
        "matched_main_jds": matched,
        "updated_parse_results": updated,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply Extraction position taxonomy v2 to existing main-system JD parse results."
    )
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--database-url", default=settings.DATABASE_URL)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    print(json.dumps(apply(args.package, args.database_url, check=args.check)))


if __name__ == "__main__":
    main()
