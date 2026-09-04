from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.core.database import create_database  # noqa: E402
from app.models.standard_position import StandardPosition  # noqa: E402


DEFAULT_CATALOG = ROOT / "config" / "position_taxonomy_catalog.v3.json"


def _load(path: Path) -> tuple[dict[str, dict[str, object]], list[dict[str, str]], str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "position-taxonomy-catalog.v3":
        raise ValueError("catalog schema must be position-taxonomy-catalog.v3")
    catalog_version = payload.get("catalog_version")
    if not isinstance(catalog_version, str) or not catalog_version.strip():
        raise ValueError("catalog_version must be non-empty")
    families = payload.get("families")
    if not isinstance(families, list) or not families:
        raise ValueError("catalog families must be a non-empty list")
    family_map: dict[str, dict[str, object]] = {}
    for family in families:
        code = family.get("code") if isinstance(family, dict) else None
        domains = family.get("allowed_skill_domains") if isinstance(family, dict) else None
        if not isinstance(code, str) or not code.strip():
            raise ValueError("family code must be non-empty")
        if code in family_map:
            raise ValueError(f"duplicate family code: {code}")
        if not isinstance(domains, list) or not domains or not all(isinstance(value, str) for value in domains):
            raise ValueError(f"family allowed_skill_domains must be non-empty: {code}")
        family_map[code] = family
    positions = payload.get("positions")
    if not isinstance(positions, list) or not positions:
        raise ValueError("catalog positions must be a non-empty list")
    codes: set[str] = set()
    names: set[str] = set()
    for item in positions:
        code = item.get("code") if isinstance(item, dict) else None
        name = item.get("name") if isinstance(item, dict) else None
        if not isinstance(code, str) or not code.strip():
            raise ValueError("position code must be non-empty")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"position name must be non-empty: {code}")
        if code in codes or name in names:
            raise ValueError(f"duplicate position taxonomy entry: {code}")
        codes.add(code)
        names.add(name)
        if item.get("family_code") not in family_map:
            raise ValueError(f"unknown family_code for position: {code}")
    return family_map, positions, catalog_version


def sync(path: Path, database_url: str, *, check: bool) -> dict[str, int]:
    families, positions, catalog_version = _load(path)
    database = create_database(database_url)
    created = updated = 0
    try:
        with database.session_factory() as session:
            legacy = session.scalar(
                select(StandardPosition).where(
                    StandardPosition.position_code.is_(None),
                    StandardPosition.taxonomy_family_code.is_not(None),
                    StandardPosition.status == "catalog",
                )
            )
            if legacy is not None:
                raise RuntimeError(
                    "legacy position taxonomy rows exist; run replace_position_taxonomy_catalog.py explicitly"
                )
            for item in positions:
                code = item["code"].strip()
                name = item["name"].strip()
                row = session.scalar(
                    select(StandardPosition).where(
                        StandardPosition.position_code == code
                    )
                )
                if row is None:
                    row = StandardPosition(
                        id=str(uuid4()),
                        position_code=code,
                        position_name=name,
                        taxonomy_family_code=item["family_code"],
                        taxonomy_family_name=families[item["family_code"]]["name"],
                        skill_domain_codes=families[item["family_code"]]["allowed_skill_domains"],
                        definition=item["definition"],
                        aliases=item["aliases"],
                        include_when=item["include_when"],
                        exclude_when=item["exclude_when"],
                        confusable_with=item["confusable_with"],
                        taxonomy_version=catalog_version,
                        lifecycle_status=item["lifecycle_status"],
                        deprecated_at=(
                            datetime.fromisoformat(item["deprecated_at"])
                            if item["deprecated_at"]
                            else None
                        ),
                        replaced_by=item["replaced_by"],
                        sample_support_status=item["sample_support_status"],
                        core_responsibilities=[],
                        required_skills=[],
                        bonus_skills=[],
                        industry_scenarios=[],
                        status="catalog",
                    )
                    session.add(row)
                    created += 1
                elif (
                    row.position_name != name
                    or row.taxonomy_family_code != item["family_code"]
                    or row.taxonomy_family_name != families[item["family_code"]]["name"]
                    or row.skill_domain_codes != families[item["family_code"]]["allowed_skill_domains"]
                    or row.definition != item["definition"]
                    or row.aliases != item["aliases"]
                    or row.include_when != item["include_when"]
                    or row.exclude_when != item["exclude_when"]
                    or row.confusable_with != item["confusable_with"]
                    or row.taxonomy_version != catalog_version
                    or row.lifecycle_status != item["lifecycle_status"]
                    or row.replaced_by != item["replaced_by"]
                    or row.sample_support_status != item["sample_support_status"]
                ):
                    row.position_name = name
                    row.taxonomy_family_code = item["family_code"]
                    row.taxonomy_family_name = families[item["family_code"]]["name"]
                    row.skill_domain_codes = families[item["family_code"]]["allowed_skill_domains"]
                    row.definition = item["definition"]
                    row.aliases = item["aliases"]
                    row.include_when = item["include_when"]
                    row.exclude_when = item["exclude_when"]
                    row.confusable_with = item["confusable_with"]
                    row.taxonomy_version = catalog_version
                    row.lifecycle_status = item["lifecycle_status"]
                    row.deprecated_at = (
                        datetime.fromisoformat(item["deprecated_at"])
                        if item["deprecated_at"]
                        else None
                    )
                    row.replaced_by = item["replaced_by"]
                    row.sample_support_status = item["sample_support_status"]
                    updated += 1
            if check:
                session.rollback()
            else:
                session.commit()
    finally:
        database.dispose()
    return {"catalog_positions": len(positions), "created": created, "updated": updated}


def main() -> None:
    parser = argparse.ArgumentParser(description="Synchronize the standard position catalog.")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--database-url", default=settings.DATABASE_URL)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    print(json.dumps(sync(args.catalog, args.database_url, check=args.check)))


if __name__ == "__main__":
    main()
