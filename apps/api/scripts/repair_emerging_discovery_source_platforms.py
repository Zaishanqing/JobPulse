"""Attach audited platform identity to the 214-JD discovery package."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy import or_


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.core.database import create_database  # noqa: E402
from app.models.jd import JobDescription  # noqa: E402


DATASET = "emerging-discovery-3day-v1"
EXPECTED_COUNT = 214


def main() -> None:
    candidates = (
        ROOT / "data/extraction-audit/capability-evolution-2026-selection.json",
        ROOT.parent.parent / "data/extraction-audit/capability-evolution-2026-selection.json",
    )
    manifest_path = next((path for path in candidates if path.is_file()), candidates[0])
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list) or len(records) != EXPECTED_COUNT:
        raise RuntimeError("discovery source manifest must contain exactly 214 records")
    platforms = {
        str(row["document_id"]): str(row["source_platform"]).strip().casefold()
        for row in records
    }
    database = create_database(settings.DATABASE_URL)
    session = database.session_factory()
    try:
        rows = (
            session.query(JobDescription)
            .filter(
                or_(
                    JobDescription.source_name.like(f"{DATASET}:%"),
                    JobDescription.source_name.like(f"retired-{DATASET}:%"),
                    JobDescription.source_name.like(f"superseded-{DATASET}:%"),
                )
            )
            .all()
        )
        if len(rows) not in {EXPECTED_COUNT, EXPECTED_COUNT * 2}:
            raise RuntimeError(
                f"expected {EXPECTED_COUNT} active rows (or one legacy duplicate set), found {len(rows)}"
            )
        repaired = 0
        retired = 0
        by_source_name = {str(row.source_name): row for row in rows}
        matched_ids: set[str] = set()
        for document_id, platform in platforms.items():
            desired = f"{DATASET}:{platform}:{document_id}"
            legacy_name = f"{DATASET}:{document_id}"
            retired_name = f"retired-{DATASET}:{document_id}"
            superseded_name = f"superseded-{DATASET}:{document_id}"
            candidates_for_document = [
                row
                for name in (desired, legacy_name, retired_name, superseded_name)
                if (row := by_source_name.get(name)) is not None
            ]
            if not candidates_for_document:
                raise RuntimeError(f"database discovery JD is missing for {document_id}")
            matched_ids.update(row.id for row in candidates_for_document)
            # The earlier row is the already-published immutable fact set. A
            # later startup-created duplicate may not have completed publication.
            active = min(candidates_for_document, key=lambda row: (row.created_at, row.id))
            for duplicate in candidates_for_document:
                if duplicate.id == active.id:
                    continue
                duplicate.source_name = superseded_name
                duplicate.is_deprecated = True
                retired += 1
            if active.source_name != desired:
                active.source_name = desired
                repaired += 1
            active.is_deprecated = False
        if matched_ids != {row.id for row in rows}:
            raise RuntimeError("database contains discovery JDs outside the 214-row manifest")
        session.commit()
        print(
            f"source platforms ready: active={EXPECTED_COUNT} repaired={repaired} retired={retired}"
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        database.dispose()


if __name__ == "__main__":
    main()
