from sqlalchemy import select

from app.infrastructure.sqlalchemy.query_base import QuerySession, position_build_version
from app.models import (
    BuildInputWatermarkRecord,
    GraphBuildRun,
    GraphVersion,
    PositionCategory,
    Skill,
    SkillAlias,
    SkillClassification,
    SkillCategory,
    SkillTaxonomyNode,
    StandardPosition,
)
from jobgraph_contracts.skill_relations import (
    SkillRelationSnapshotV1,
    SkillRelationSnapshotV2,
)


class CatalogQueryMixin(QuerySession):
    def positions(self) -> list[dict]:
        rows = self.session.scalars(
            select(StandardPosition).where(
                StandardPosition.current_version_id.is_not(None),
                StandardPosition.status == "active",
            )
        ).all()
        result = []
        for row in rows:
            version = self.session.get(GraphVersion, row.current_version_id)
            if version is None:
                raise RuntimeError(
                    f"current graph version {row.current_version_id} does not exist"
                )
            build_run = self.session.get(GraphBuildRun, version.build_run_id)
            if build_run is None:
                raise RuntimeError(
                    f"published graph version {version.id} has no build run"
                )
            snapshot = dict(version.snapshot or {})
            sample_stats = dict(snapshot.get("sample_stats") or {})
            skill_count = len(snapshot.get("skill_relations") or [])
            sample_count = int(sample_stats.get("included_samples") or 0)
            result.append({
                "position_id": row.position_id,
                "name": row.name,
                "category_code": row.category_code,
                "current_version_id": row.current_version_id,
                "current_version_number": position_build_version(
                    self.session, build_run
                ),
                "sample_count": sample_count,
                "skill_count": skill_count,
                "published_at": version.published_at.isoformat(),
                "release_id": version.release_id,
                "quality_state": "thin" if sample_count < 3 or skill_count < 3 else "ready",
            })
        return result

    def integration_positions(self) -> list[dict]:
        rows = self.session.scalars(
            select(StandardPosition).where(StandardPosition.status == "active")
        ).all()
        return [
            {
                "position_id": row.position_id,
                "position_code": row.position_code,
                "name": row.name,
                "category_code": row.category_code,
                "taxonomy_version": row.taxonomy_version,
                "sample_support_status": row.sample_support_status,
                "current_version_id": row.current_version_id,
            }
            for row in rows
        ]

    def integration_position_references(self) -> list[dict]:
        rows = self.session.scalars(
            select(StandardPosition)
            .where(
                StandardPosition.status == "active",
                StandardPosition.current_version_id.is_not(None),
            )
            .order_by(StandardPosition.position_id)
        ).all()
        result = []
        for position in rows:
            graph = self.graph(position.position_id)
            result.append(
                {
                    "position_id": position.position_id,
                    "position_name": position.name,
                    "graph_version_id": position.current_version_id,
                    "required_skills": [
                        {
                            "normalized_skill_id": relation["skill_id"],
                            "raw_skill": relation.get("canonical_name", relation["skill_id"]),
                        }
                        for relation in graph.get("skill_relations", [])
                        if relation.get("skill_id")
                        and relation.get("primary_modality") == "required"
                    ],
                }
            )
        return result

    def skill_relation_snapshot(self, position_id: str) -> dict | None:
        source = self._skill_relation_snapshot_source(position_id)
        if source is None:
            return None
        _position, version, watermark, graph, evidence_by_skill = source
        payload = SkillRelationSnapshotV1(
            contract_version="skill-relation-snapshot.v1",
            position_id=position_id,
            graph_version_id=version.id,
            release_id=version.release_id,
            watermark_version=watermark.lineage_version,
            graph_version=version.source_version,
            authority_state=(
                "authoritative"
                if watermark.validation_state == "present"
                else "observed"
            ),
            generated_at=version.published_at,
            relations=[
                {
                    "skill_id": relation["skill_id"],
                    "canonical_name": relation["canonical_name"],
                    "category_code": relation["category_code"],
                    "subcategory_code": relation.get("subcategory_code"),
                    "primary_modality": relation.get("primary_modality", "unknown"),
                    "weight": relation["weight"],
                    "confidence": relation["confidence"],
                    "importance_level": relation["importance_level"],
                    "evidence_refs": evidence_by_skill.get(
                        str(relation["skill_id"]), []
                    ),
                }
                for relation in graph.get("skill_relations", [])
            ],
        )
        return payload.model_dump(mode="json")

    def skill_relation_snapshot_v2(self, position_id: str) -> dict | None:
        source = self._skill_relation_snapshot_source(position_id)
        if source is None:
            return None
        _position, version, watermark, graph, evidence_by_skill = source
        payload = SkillRelationSnapshotV2(
            contract_version="skill-relation-snapshot.v2",
            position_id=position_id,
            graph_version_id=version.id,
            release_id=version.release_id,
            watermark_version=watermark.lineage_version,
            graph_version=version.source_version,
            authority_state=(
                "authoritative"
                if watermark.validation_state == "present"
                else "observed"
            ),
            generated_at=version.published_at,
            relations=[
                {
                    "skill_id": relation["skill_id"],
                    "canonical_name": relation["canonical_name"],
                    "classifications": relation.get("classifications", []),
                    "taxonomy_version": relation.get("taxonomy_version"),
                    "primary_modality": relation.get("primary_modality", "unknown"),
                    "weight": relation["weight"],
                    "confidence": relation["confidence"],
                    "importance_level": relation["importance_level"],
                    "evidence_refs": evidence_by_skill.get(
                        str(relation["skill_id"]), []
                    ),
                }
                for relation in graph.get("skill_relations", [])
            ],
        )
        return payload.model_dump(mode="json")

    def _skill_relation_snapshot_source(self, position_id: str):
        position = self.session.scalar(
            select(StandardPosition).where(
                StandardPosition.position_id == position_id,
                StandardPosition.status == "active",
                StandardPosition.current_version_id.is_not(None),
            )
        )
        if position is None:
            return None
        version = self.session.get(GraphVersion, position.current_version_id)
        if version is None:
            return None
        watermark = self.session.scalar(
            select(BuildInputWatermarkRecord).where(
                BuildInputWatermarkRecord.build_run_id == version.build_run_id
            )
        )
        if watermark is None:
            return None
        graph = self.graph(position_id)
        evidence_by_skill: dict[str, list[dict]] = {}
        for evidence in graph.get("evidence_summary", []):
            skill_id = evidence.get("skill_id")
            if skill_id:
                evidence_by_skill.setdefault(str(skill_id), []).append(
                    {
                        "support_id": evidence["support_id"],
                        "evidence_id": evidence["evidence_id"],
                        "document_id": evidence["document_id"],
                        "requirement_id": evidence["requirement_id"],
                    }
                )
        return position, version, watermark, graph, evidence_by_skill

    def position(self, position_id: str) -> dict | None:
        row = self.session.scalar(
            select(StandardPosition).where(
                StandardPosition.position_id == position_id,
                StandardPosition.current_version_id.is_not(None),
                StandardPosition.status == "active",
            )
        )
        if row is None:
            return None
        return {
            "position_id": row.position_id,
            "name": row.name,
            "category_code": row.category_code,
            "status": row.status,
            "current_version_id": row.current_version_id,
        }

    def skills(self) -> list[dict]:
        aliases_by_skill: dict[str, list[str]] = {}
        for alias in self.session.scalars(select(SkillAlias)).all():
            aliases_by_skill.setdefault(alias.skill_id, []).append(alias.alias)
        result = []
        for row in self.session.scalars(select(Skill)).all():
            classifications = self._skill_classifications(row.skill_id)
            result.append({
                "skill_id": row.skill_id,
                "canonical_name": row.canonical_name,
                "category_code": row.category_code,
                "classifications": classifications,
                "taxonomy_version": row.taxonomy_version,
                "status": row.status,
                "aliases": aliases_by_skill.get(row.skill_id, []),
            })
        return result

    def skill(self, skill_id: str) -> dict | None:
        row = self.session.scalar(select(Skill).where(Skill.skill_id == skill_id))
        if row is None:
            return None
        return {
            "skill_id": row.skill_id,
            "canonical_name": row.canonical_name,
            "category_code": row.category_code,
            "subcategory_code": row.subcategory_code,
            "classifications": self._skill_classifications(row.skill_id),
            "taxonomy_version": row.taxonomy_version,
        }

    def _skill_classifications(self, skill_id: str) -> list[dict]:
        rows = self.session.execute(
            select(SkillClassification, SkillTaxonomyNode)
            .join(
                SkillTaxonomyNode,
                SkillTaxonomyNode.id == SkillClassification.taxonomy_node_id,
            )
            .where(SkillClassification.skill_id == skill_id)
            .order_by(
                SkillClassification.facet,
                SkillClassification.is_primary.desc(),
                SkillTaxonomyNode.code,
            )
        ).all()
        return [
            {
                "facet": relation.facet,
                "code": node.code,
                "name_zh": node.name_zh,
                "name_en": node.name_en,
                "is_primary": relation.is_primary,
            }
            for relation, node in rows
        ]

    def category_tree(self, kind: str) -> list[dict]:
        model = SkillCategory if kind == "skill" else PositionCategory
        return [
            {"code": row.code, "name": row.name, "parent_code": row.parent_code}
            for row in self.session.scalars(select(model)).all()
        ]
