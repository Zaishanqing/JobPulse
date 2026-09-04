"""Export the authoritative main-system position taxonomy v3 catalog."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
AUTHORITATIVE_TAXONOMY_PATH = ROOT / "config" / "position_taxonomy_catalog.v3.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import Settings  # noqa: E402
from app.core.database import create_database  # noqa: E402
from app.models.standard_position import StandardPosition  # noqa: E402
from jobgraph_contracts.position_catalog_v3 import (  # noqa: E402
    ResolvedPositionCatalogV3,
    build_resolved_position_catalog_v3,
)


def _authoritative_catalog() -> ResolvedPositionCatalogV3:
    taxonomy_payload = json.loads(
        AUTHORITATIVE_TAXONOMY_PATH.read_text(encoding="utf-8")
    )
    if taxonomy_payload.get("catalog_version") != "position-taxonomy.v3.0.0":
        raise ValueError("Authoritative position taxonomy version is invalid")
    taxonomy_positions = taxonomy_payload.get("positions")
    if not isinstance(taxonomy_positions, list) or not taxonomy_positions:
        raise ValueError("Authoritative position taxonomy has no positions")
    expected_codes = {
        str(item.get("code", "")).strip()
        for item in taxonomy_positions
        if isinstance(item, dict)
    }
    if "" in expected_codes or len(expected_codes) != len(taxonomy_positions):
        raise ValueError("Authoritative position taxonomy contains invalid codes")
    database = create_database(Settings().DATABASE_URL)
    try:
        with database.session_factory() as session:
            rows = list(
                session.scalars(
                    select(StandardPosition)
                    .where(
                        StandardPosition.taxonomy_version == "position-taxonomy.v3.0.0",
                        StandardPosition.status == "catalog",
                    )
                    .order_by(
                        StandardPosition.taxonomy_family_code,
                        StandardPosition.position_code,
                    )
                )
            )
        if not rows:
            raise ValueError("No resolved standard positions found")
        actual_codes = {
            str(row.position_code or "").strip()
            for row in rows
        }
        if actual_codes != expected_codes or len(rows) != len(expected_codes):
            missing = sorted(expected_codes - actual_codes)
            unexpected = sorted(actual_codes - expected_codes)
            raise ValueError(
                "Main-system position catalog is incomplete: "
                f"expected={len(expected_codes)}, actual={len(rows)}, "
                f"missing={missing}, unexpected={unexpected}"
            )
        positions = []
        for row in rows:
            position_code = str(row.position_code or "").strip()
            family_code = str(row.taxonomy_family_code or "").strip()
            family_name = str(row.taxonomy_family_name or "").strip()
            if not position_code or not family_code or not family_name:
                raise ValueError(f"Incomplete v3 position: {row.id}")
            positions.append(
                {
                    "main_system_position_id": row.id,
                    "position_code": position_code,
                    "position_name": row.position_name,
                    "family_code": family_code,
                    "family_name": family_name,
                    "definition": row.definition,
                    "aliases": list(row.aliases or []),
                    "include_when": list(row.include_when or []),
                    "exclude_when": list(row.exclude_when or []),
                    "confusable_with": list(row.confusable_with or []),
                    "lifecycle_status": row.lifecycle_status,
                    "deprecated_at": (row.deprecated_at.isoformat() if row.deprecated_at else None),
                    "replaced_by": row.replaced_by,
                    "sample_support_status": row.sample_support_status,
                }
            )
        return build_resolved_position_catalog_v3(positions)
    finally:
        database.dispose()


def export_catalog(
    output: Path,
    *,
    verify_existing: bool = False,
) -> ResolvedPositionCatalogV3:
    payload = _authoritative_catalog()
    if verify_existing:
        existing = ResolvedPositionCatalogV3.model_validate_json(
            output.read_text(encoding="utf-8")
        )
        if existing != payload:
            raise ValueError(
                "Existing position catalog does not match the current "
                "authoritative main-system snapshot"
            )
        return existing
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                payload.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--verify-existing", action="store_true")
    args = parser.parse_args()
    result = export_catalog(args.output, verify_existing=args.verify_existing)
    print(
        json.dumps(
            {
                "position_count": result.position_count,
                "verified_existing": args.verify_existing,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
