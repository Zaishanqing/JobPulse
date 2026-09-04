from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.core.database import create_database  # noqa: E402
from app.models.skill import Skill  # noqa: E402
from app.models.skill_alias import SkillAlias  # noqa: E402
from app.models.skill_catalog_version import SkillCatalogVersion  # noqa: E402
from app.models.skill_taxonomy import (  # noqa: E402
    SkillClassification,
    SkillTaxonomyNode,
)
from app.models.user import User  # noqa: E402
from jobgraph_contracts.skill_taxonomy import (  # noqa: E402
    SkillClassificationSetV1,
)


DEFAULT_CATALOG = ROOT / "config" / "skill_taxonomy_catalog.v1.json"


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "skill-taxonomy-snapshot.v1":
        raise ValueError("catalog schema must be skill-taxonomy-snapshot.v1")
    if payload.get("catalog_version") != "skill-taxonomy-catalog.v1":
        raise ValueError("catalog_version must be skill-taxonomy-catalog.v1")
    skills = payload.get("skills")
    if not isinstance(skills, dict) or not skills:
        raise ValueError("catalog skills must be a non-empty object")
    node_status: dict[tuple[str, str], str] = {}
    for node in payload.get("nodes", []):
        key = (node.get("facet"), node.get("code"))
        if (
            key[0] not in {"concept_class", "technology_kind", "domain"}
            or not isinstance(key[1], str)
            or not key[1]
            or node.get("status") not in {"active", "inactive"}
        ):
            raise ValueError(f"invalid taxonomy node: {node!r}")
        if key in node_status:
            raise ValueError(f"duplicate taxonomy node: {key[0]}:{key[1]}")
        node_status[key] = node["status"]
    for skill_id, entry in skills.items():
        review = entry.get("review")
        if (
            not isinstance(review, dict)
            or review.get("status") != "approved"
            or review.get("review_basis") != "canonical_identity"
            or review.get("domain_decision")
            not in {"classified", "not_applicable"}
        ):
            raise ValueError(f"skill {skill_id} is not approved")
        classified = SkillClassificationSetV1(
            skill_id=skill_id,
            canonical_name=entry["canonical_name"],
            classifications=entry["classifications"],
        )
        relation_keys = {
            (relation.facet, relation.code)
            for relation in classified.classifications
        }
        missing_nodes = relation_keys - set(node_status)
        if missing_nodes:
            facet, code = sorted(missing_nodes)[0]
            raise ValueError(
                f"skill {skill_id} references missing taxonomy node {facet}:{code}"
            )
        inactive_nodes = sorted(
            key for key in relation_keys if node_status[key] != "active"
        )
        if inactive_nodes:
            facet, code = inactive_nodes[0]
            raise ValueError(
                f"skill {skill_id} references inactive taxonomy node {facet}:{code}"
            )
        has_domain = any(
            relation.facet == "domain" for relation in classified.classifications
        )
        if has_domain != (review["domain_decision"] == "classified"):
            raise ValueError(f"skill {skill_id} domain decision conflicts with relations")
    return payload


def sync(path: Path, database_url: str, *, check: bool) -> dict[str, int]:
    payload = _load(path)
    database = create_database(database_url)
    created_skills = created_nodes = replaced_relations = 0
    snapshot_seeded = 0
    try:
        with database.session_factory() as session:
            nodes: dict[tuple[str, str], SkillTaxonomyNode] = {}
            for item in payload["nodes"]:
                key = (item["facet"], item["code"])
                row = session.execute(
                    select(SkillTaxonomyNode).where(
                        SkillTaxonomyNode.facet == key[0],
                        SkillTaxonomyNode.code == key[1],
                    )
                ).scalar_one_or_none()
                if row is None:
                    row = SkillTaxonomyNode(
                        facet=key[0],
                        code=key[1],
                        name_zh=item["name_zh"],
                        name_en=item.get("name_en"),
                        status=item["status"],
                    )
                    session.add(row)
                    session.flush()
                    created_nodes += 1
                elif (
                    row.name_zh != item["name_zh"]
                    or row.name_en != item.get("name_en")
                    or row.status != item["status"]
                ):
                    raise ValueError(f"taxonomy node conflict: {key[0]}:{key[1]}")
                nodes[key] = row

            for catalog_code, entry in payload["skills"].items():
                by_code = session.execute(
                    select(Skill).where(Skill.catalog_code == catalog_code)
                ).scalar_one_or_none()
                by_name = session.execute(
                    select(Skill).where(Skill.skill_name == entry["canonical_name"])
                ).scalar_one_or_none()
                if by_code is not None and by_name is not None and by_code.id != by_name.id:
                    raise ValueError(f"skill identity conflict: {catalog_code}")
                skill = by_code or by_name
                if skill is None:
                    skill = Skill(
                        catalog_code=catalog_code,
                        skill_name=entry["canonical_name"],
                    )
                    session.add(skill)
                    session.flush()
                    created_skills += 1
                elif skill.catalog_code is None:
                    skill.catalog_code = catalog_code
                    session.flush()
                elif skill.skill_name != entry["canonical_name"]:
                    raise ValueError(f"skill canonical name conflict: {catalog_code}")

                previous = session.execute(
                    select(SkillClassification).where(
                        SkillClassification.skill_id == skill.id
                    )
                ).scalars().all()
                for relation in previous:
                    session.delete(relation)
                session.flush()
                for relation in entry["classifications"]:
                    node = nodes[(relation["facet"], relation["code"])]
                    session.add(
                        SkillClassification(
                            skill_id=skill.id,
                            taxonomy_node_id=node.id,
                            facet=node.facet,
                            is_primary=relation["is_primary"],
                        )
                    )
                    replaced_relations += 1
                session.flush()
            if not check:
                snapshot_seeded = int(_ensure_catalog_snapshot(session))
            if check:
                session.rollback()
            else:
                session.commit()
    finally:
        database.dispose()
    return {
        "catalog_skills": len(payload["skills"]),
        "created_skills": created_skills,
        "created_nodes": created_nodes,
        "classification_relations": replaced_relations,
        "snapshot_seeded": snapshot_seeded,
    }


def _ensure_catalog_snapshot(session) -> bool:
    """Seed the frozen catalog snapshot used by validation and import paths."""
    if session.query(SkillCatalogVersion).first() is not None:
        return False
    user = session.query(User).order_by(User.created_at.asc()).first()
    if user is None:
        return False
    skills = list(session.scalars(select(Skill).order_by(Skill.id.asc())).all())
    classification_rows = session.execute(
        select(SkillClassification, SkillTaxonomyNode)
        .join(
            SkillTaxonomyNode,
            SkillTaxonomyNode.id == SkillClassification.taxonomy_node_id,
        )
        .order_by(
            SkillClassification.skill_id,
            SkillClassification.facet,
            SkillTaxonomyNode.code,
        )
    ).all()
    aliases = list(
        session.scalars(
            select(SkillAlias).order_by(
                SkillAlias.skill_id.asc(), SkillAlias.alias.asc()
            )
        ).all()
    )
    snapshot = {
        "schema": "skill-catalog-snapshot.v1",
        "taxonomy_catalog_version": "skill-taxonomy-catalog.v1",
        "skills": [
            {
                "skill_id": skill.id,
                "catalog_code": skill.catalog_code,
                "skill_name": skill.skill_name,
                "category": skill.category,
                "description": skill.description,
                "parent_skill_id": skill.parent_skill_id,
                "status": skill.status,
                "redirect_target_skill_id": skill.redirect_target_skill_id,
            }
            for skill in skills
        ],
        "classifications": [
            {
                "skill_id": relation.skill_id,
                "facet": relation.facet,
                "code": node.code,
                "is_primary": relation.is_primary,
            }
            for relation, node in classification_rows
        ],
        "aliases": [
            {"skill_id": alias.skill_id, "alias": alias.alias}
            for alias in aliases
        ],
    }
    session.add(
        SkillCatalogVersion(
            version_number=1,
            catalog_version="skill-catalog.v1",
            snapshot=snapshot,
            change_summary={
                "source": "startup-sync",
                "reason": "seed frozen snapshot for fast catalog reads",
            },
            published_by=user.id,
        )
    )
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Atomically synchronize the reviewed skill taxonomy catalog."
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--database-url", default=settings.DATABASE_URL)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    print(json.dumps(sync(args.catalog, args.database_url, check=args.check)))


if __name__ == "__main__":
    main()
