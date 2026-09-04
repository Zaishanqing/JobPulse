"""Project the reviewed main-system skill taxonomy catalog into KG storage."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.application.use_cases import ImportCapabilitySkillSnapshotUseCase  # noqa: E402
from app.config import Settings  # noqa: E402
from app.database import create_database  # noqa: E402
from app.infrastructure.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork  # noqa: E402
from app.models import User  # noqa: E402
from jobgraph_contracts.catalog import StandardSkillSnapshotV2  # noqa: E402
from jobgraph_contracts.skill_taxonomy import (  # noqa: E402
    SkillClassificationSetV1,
    SkillClassificationV1,
)


DEFAULT_CATALOG = ROOT / "config" / "skill_taxonomy_catalog.v1.json"


def load_snapshots(path: Path) -> tuple[str, list[StandardSkillSnapshotV2]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "skill-taxonomy-snapshot.v1":
        raise ValueError("catalog schema must be skill-taxonomy-snapshot.v1")
    if payload.get("catalog_version") != "skill-taxonomy-catalog.v1":
        raise ValueError("catalog_version must be skill-taxonomy-catalog.v1")

    node_by_key: dict[tuple[str, str], dict] = {}
    for node in payload.get("nodes", []):
        key = (node.get("facet"), node.get("code"))
        if key in node_by_key:
            raise ValueError(f"duplicate taxonomy node: {key[0]}:{key[1]}")
        if node.get("status") != "active":
            raise ValueError(f"taxonomy node must be active: {key[0]}:{key[1]}")
        node_by_key[key] = node

    raw_skills = payload.get("skills")
    if not isinstance(raw_skills, dict) or not raw_skills:
        raise ValueError("catalog skills must be a non-empty object")

    classified: list[SkillClassificationSetV1] = []
    for skill_id, entry in raw_skills.items():
        review = entry.get("review")
        if (
            not isinstance(review, dict)
            or review.get("status") != "approved"
            or review.get("review_basis") != "canonical_identity"
            or review.get("domain_decision") not in {"classified", "not_applicable"}
        ):
            raise ValueError(f"skill {skill_id} is not approved")
        item = SkillClassificationSetV1(
            skill_id=skill_id,
            canonical_name=entry["canonical_name"],
            classifications=entry["classifications"],
        )
        has_domain = any(relation.facet == "domain" for relation in item.classifications)
        if has_domain != (review["domain_decision"] == "classified"):
            raise ValueError(f"skill {skill_id} domain decision conflicts with relations")
        classified.append(item)

    taxonomy_version = "skill-taxonomy-catalog-current"
    snapshots: list[StandardSkillSnapshotV2] = []
    for item in classified:
        relations: list[SkillClassificationV1] = []
        for relation in item.classifications:
            node = node_by_key.get((relation.facet, relation.code))
            if node is None:
                raise ValueError(
                    f"skill {item.skill_id} references missing taxonomy node "
                    f"{relation.facet}:{relation.code}"
                )
            relations.append(
                SkillClassificationV1(
                    facet=relation.facet,
                    code=relation.code,
                    name_zh=node["name_zh"],
                    name_en=node.get("name_en"),
                    is_primary=relation.is_primary,
                )
            )
        snapshots.append(
            StandardSkillSnapshotV2(
                skill_id=item.skill_id,
                canonical_name=item.canonical_name,
                classifications=relations,
                taxonomy_version=taxonomy_version,
            )
        )
    return taxonomy_version, snapshots


def sync(path: Path, settings: Settings) -> dict[str, int | str]:
    taxonomy_version, snapshots = load_snapshots(path)
    database = create_database(settings)
    try:
        with database.session_factory() as session:
            actor_id = session.scalar(
                select(User.id).where(
                    User.username == settings.service_username,
                    User.role == "integration_service",
                )
            )
        if actor_id is None:
            raise ValueError("configured KG integration service identity does not exist")

        importer = ImportCapabilitySkillSnapshotUseCase(
            lambda: SqlAlchemyUnitOfWork(database.session_factory)
        )
        context = {
            "source_system": "main-system",
            "source_user_id": "reviewed-skill-taxonomy-catalog",
            "source_user_role": "system",
        }
        for index, snapshot in enumerate(snapshots, start=1):
            importer.execute(
                snapshot,
                actor_id,
                f"catalog-sync-{index}",
                context,
            )
        return {
            "catalog_skills": len(snapshots),
            "taxonomy_version": taxonomy_version,
        }
    finally:
        database.engine.dispose()


def main() -> int:
    result = sync(DEFAULT_CATALOG, Settings.from_env())
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
