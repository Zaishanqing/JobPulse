"""Import a main-system resolved-position catalog into the KG-owned database."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import sys
from pathlib import Path
from collections.abc import Iterator

from sqlalchemy import select, text
from sqlalchemy.orm import Session


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.database import create_database  # noqa: E402
from app.domain.policies import RELATION_ALGORITHM_CONFIG  # noqa: E402
from app.models import (  # noqa: E402
    AlgorithmConfig,
    PositionCategory,
    StandardPosition,
)
from jobgraph_contracts.position_catalog_v3 import ResolvedPositionCatalogV3  # noqa: E402


IMMUTABLE_POSITION_REFERENCE_TABLES = ("graph_versions", "relation_claims")


@contextmanager
def _allow_position_identity_cascade(session: Session) -> Iterator[None]:
    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        for table_name in IMMUTABLE_POSITION_REFERENCE_TABLES:
            session.execute(text(f"DROP TRIGGER IF EXISTS trg_{table_name}_reject_update"))
    elif dialect == "postgresql":
        for table_name in IMMUTABLE_POSITION_REFERENCE_TABLES:
            session.execute(
                text(
                    f"ALTER TABLE {table_name} "
                    f"DISABLE TRIGGER trg_{table_name}_reject_update"
                )
            )
    else:
        raise RuntimeError(f"Unsupported KG catalog import dialect: {dialect}")
    try:
        yield
        session.flush()
    finally:
        if dialect == "sqlite":
            for table_name in IMMUTABLE_POSITION_REFERENCE_TABLES:
                session.execute(
                    text(
                        f"CREATE TRIGGER trg_{table_name}_reject_update "
                        f"BEFORE UPDATE ON {table_name} "
                        f"BEGIN SELECT RAISE(ABORT, '{table_name} is immutable'); END"
                    )
                )
        else:
            for table_name in IMMUTABLE_POSITION_REFERENCE_TABLES:
                session.execute(
                    text(
                        f"ALTER TABLE {table_name} "
                        f"ENABLE TRIGGER trg_{table_name}_reject_update"
                    )
                )


def import_catalog(path: Path) -> dict[str, int]:
    catalog = ResolvedPositionCatalogV3.model_validate_json(
        path.read_text(encoding="utf-8")
    )
    taxonomy_version = catalog.taxonomy_version
    positions = catalog.positions
    database = create_database(Settings.from_env())
    created = migrated = updated = 0
    try:
        with database.session_factory() as session:
            immutable_counts_before = {
                table_name: session.execute(
                    text(f"SELECT COUNT(*) FROM {table_name}")
                ).scalar_one()
                for table_name in IMMUTABLE_POSITION_REFERENCE_TABLES
            }
            with _allow_position_identity_cascade(session):
                for item in positions:
                    position_code = item.position_code
                    main_system_position_id = item.main_system_position_id
                    position_name = item.position_name
                    family_code = item.family_code
                    family_name = item.family_name
                    lifecycle_status = item.lifecycle_status
                    sample_support_status = item.sample_support_status
                    category = session.scalar(
                        select(PositionCategory).where(
                            PositionCategory.code == family_code
                        )
                    )
                    if category is None:
                        session.add(
                            PositionCategory(
                                code=family_code,
                                name=family_name,
                                parent_code=None,
                            )
                        )
                    elif category.name != family_name:
                        category.name = family_name
                    position = session.scalar(
                        select(StandardPosition).where(
                            StandardPosition.position_code == position_code
                        )
                    )
                    legacy = session.scalar(
                        select(StandardPosition).where(
                            StandardPosition.position_id == main_system_position_id
                        )
                    )
                    if position is not None and legacy is not None and legacy is not position:
                        raise ValueError(
                            "Position catalog has both legacy and code identities: "
                            f"{position_code}"
                        )
                    if position is None:
                        if legacy is None:
                            position = StandardPosition(
                                position_id=position_code,
                                position_code=position_code,
                                name=position_name,
                                category_code=family_code,
                                taxonomy_version=taxonomy_version,
                                sample_support_status=sample_support_status,
                                status=lifecycle_status,
                            )
                            session.add(position)
                            created += 1
                        else:
                            position = legacy
                            position.position_id = position_code
                            position.position_code = position_code
                            migrated += 1
                    authoritative_values = {
                        "name": position_name,
                        "category_code": family_code,
                        "taxonomy_version": taxonomy_version,
                        "sample_support_status": sample_support_status,
                        "status": lifecycle_status,
                    }
                    changed = False
                    for field, value in authoritative_values.items():
                        if getattr(position, field) != value:
                            setattr(position, field, value)
                            changed = True
                    if changed and position.position_id == position_code:
                        updated += 1
            remaining_legacy = list(
                session.scalars(
                    select(StandardPosition).where(StandardPosition.position_code.is_(None))
                )
            )
            if remaining_legacy:
                raise ValueError(
                    "KG contains standard positions absent from the authoritative "
                    "v3 identity mapping"
                )
            session.flush()
            stored_positions = list(session.scalars(select(StandardPosition)))
            expected_codes = {item.position_code for item in positions}
            stored_codes = {
                str(item.position_code or "").strip() for item in stored_positions
            }
            if len(stored_positions) != len(positions) or stored_codes != expected_codes:
                raise ValueError(
                    "KG position catalog does not exactly match the authoritative snapshot"
                )
            immutable_counts_after = {
                table_name: session.execute(
                    text(f"SELECT COUNT(*) FROM {table_name}")
                ).scalar_one()
                for table_name in IMMUTABLE_POSITION_REFERENCE_TABLES
            }
            if immutable_counts_after != immutable_counts_before:
                raise ValueError(
                    "Position identity migration changed immutable graph fact counts"
                )
            algorithm = session.scalar(
                select(AlgorithmConfig).where(AlgorithmConfig.version == "weighted-v1")
            )
            if algorithm is None:
                session.add(
                    AlgorithmConfig(
                        version="weighted-v1",
                        payload={
                            "sample_quality": {
                                "duplicate_factor": 0.6,
                                "copy_factor": 0.4,
                                "inflation_factor": 0.5,
                            },
                            **RELATION_ALGORITHM_CONFIG,
                        },
                        active=True,
                    )
                )
            session.commit()
        return {
            "position_count": len(positions),
            "created_count": created,
            "migrated_identity_count": migrated,
            "updated_count": updated,
            "mapped_position_count": len(positions),
        }
    finally:
        database.engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("catalog", type=Path)
    args = parser.parse_args()
    print(json.dumps(import_catalog(args.catalog), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
