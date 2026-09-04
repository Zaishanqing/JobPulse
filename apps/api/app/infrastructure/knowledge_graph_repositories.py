from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.domain.text_cleaning import clean_jd_text_for_display
from app.models.jd import JobDescription
from app.models.jd_parse_result import JDParseResult
from app.models.knowledge_graph_mapping import KnowledgeGraphEntityMapping
from app.models.standard_position import StandardPosition
from app.models.skill import Skill
from app.models.skill_alias import SkillAlias
from app.models.skill_taxonomy import SkillClassification, SkillTaxonomyNode
from jobgraph_contracts.skill_taxonomy import (
    SkillClassificationSetV1,
)


class SqlAlchemyKnowledgeGraphSourceRepository:
    """Read the main-system facts needed by the KG integration boundary."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._taxonomy_version_cache: str | None = None

    def document(self, document_id: str) -> JobDescription | None:
        return self._session.get(JobDescription, document_id)

    def parsed_document(self, document_id: str) -> JDParseResult | None:
        return (
            self._session.query(JDParseResult)
            .filter(JDParseResult.jd_id == document_id)
            .first()
        )

    def position(self, position_id: str) -> StandardPosition | None:
        return self._session.get(StandardPosition, position_id)

    def positions(self) -> list[StandardPosition]:
        return self._session.query(StandardPosition).order_by(StandardPosition.id).all()

    def skill(self, skill_id: str) -> Skill | None:
        return self._session.get(Skill, skill_id)

    def skills(self) -> list[Skill]:
        return self._session.query(Skill).order_by(Skill.id).all()

    def document_text(self, document_id: str) -> str | None:
        row = self.document(document_id)
        if row is None:
            return None
        return row.cleaned_text or clean_jd_text_for_display(row.raw_text)

    def skill_snapshot(self, skill_id: str) -> dict | None:
        skill = self._session.get(Skill, skill_id)
        if skill is None:
            return None
        if not skill.catalog_code:
            raise ValueError(
                f"Skill {skill_id} has no authoritative catalog_code"
            )
        aliases = (
            self._session.query(SkillAlias)
            .filter(SkillAlias.skill_id == skill_id)
            .order_by(SkillAlias.alias)
            .all()
        )
        classifications = (
            self._session.query(SkillClassification, SkillTaxonomyNode)
            .join(
                SkillTaxonomyNode,
                SkillTaxonomyNode.id == SkillClassification.taxonomy_node_id,
            )
            .filter(SkillClassification.skill_id == skill_id)
            .order_by(
                SkillClassification.facet,
                SkillClassification.is_primary.desc(),
                SkillTaxonomyNode.code,
            )
            .all()
        )
        if not classifications:
            raise ValueError(
                f"Skill {skill_id} has no authoritative classifications"
            )
        return {
            'contract_version': 'capability-skill-snapshot.v2',
            'skill_id': skill.catalog_code,
            'canonical_name': skill.skill_name,
            'aliases': [item.alias for item in aliases],
            'classifications': [
                {
                    'facet': relation.facet,
                    'code': node.code,
                    'name_zh': node.name_zh,
                    'name_en': node.name_en,
                    'is_primary': relation.is_primary,
                }
                for relation, node in classifications
            ],
            'taxonomy_version': self._taxonomy_version(),
            'status': 'active',
        }

    def _taxonomy_version(self) -> str:
        if self._taxonomy_version_cache is not None:
            return self._taxonomy_version_cache
        rows = (
            self._session.query(
                Skill.catalog_code,
                Skill.skill_name,
                SkillClassification.facet,
                SkillTaxonomyNode.code,
                SkillClassification.is_primary,
            )
            .join(SkillClassification, SkillClassification.skill_id == Skill.id)
            .join(
                SkillTaxonomyNode,
                SkillTaxonomyNode.id == SkillClassification.taxonomy_node_id,
            )
            .filter(Skill.catalog_code.is_not(None))
            .order_by(
                Skill.catalog_code,
                SkillClassification.facet,
                SkillTaxonomyNode.code,
            )
            .all()
        )
        grouped: dict[tuple[str, str], list[dict]] = {}
        for row in rows:
            grouped.setdefault((row[0], row[1]), []).append(
                {
                    "facet": row[2],
                    "code": row[3],
                    "is_primary": row[4],
                }
            )
        catalog_codes = {
            row[0]
            for row in self._session.query(Skill.catalog_code)
            .filter(Skill.catalog_code.is_not(None))
            .all()
        }
        classified_codes = {key[0] for key in grouped}
        missing = sorted(catalog_codes - classified_codes)
        if missing:
            raise ValueError(
                "Authoritative taxonomy skills are missing classifications: "
                + ", ".join(missing[:10])
            )
        self._taxonomy_version_cache = "skill-taxonomy-snapshot.v1"
        return self._taxonomy_version_cache


class SqlAlchemyKnowledgeGraphMappingRepository:
    """Own local mapping persistence without owning the transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(
        self, entity_type: str, main_system_id: str
    ) -> KnowledgeGraphEntityMapping | None:
        return (
            self._session.query(KnowledgeGraphEntityMapping)
            .filter(
                KnowledgeGraphEntityMapping.entity_type == entity_type,
                KnowledgeGraphEntityMapping.main_system_id == main_system_id,
            )
            .first()
        )

    def get_or_create(
        self, entity_type: str, main_system_id: str
    ) -> KnowledgeGraphEntityMapping:
        row = self.get(entity_type, main_system_id)
        if row is None:
            row = KnowledgeGraphEntityMapping(
                entity_type=entity_type,
                main_system_id=main_system_id,
                sync_status="pending",
            )
            self._session.add(row)
            self._session.flush()
        return row

    def list_confirmed(self, entity_type: str) -> list[KnowledgeGraphEntityMapping]:
        return (
            self._session.query(KnowledgeGraphEntityMapping)
            .filter(
                KnowledgeGraphEntityMapping.entity_type == entity_type,
                KnowledgeGraphEntityMapping.sync_status.in_(("synced", "confirmed")),
                KnowledgeGraphEntityMapping.knowledge_graph_id.is_not(None),
            )
            .all()
        )

    def list(self, entity_type: str) -> list[KnowledgeGraphEntityMapping]:
        return (
            self._session.query(KnowledgeGraphEntityMapping)
            .filter(KnowledgeGraphEntityMapping.entity_type == entity_type)
            .order_by(KnowledgeGraphEntityMapping.main_system_id)
            .all()
        )

    def clear(self, row: KnowledgeGraphEntityMapping) -> None:
        row.knowledge_graph_id = None
        row.sync_version = None
        row.sync_status = "pending"
        row.last_error_code = None
        row.last_error_message = None
        row.last_trace_id = None
        row.synced_at = None
        self._session.flush()

    def flush(self, row: KnowledgeGraphEntityMapping, *, refresh: bool = False) -> None:
        self._session.flush()
        if refresh:
            self._session.refresh(row)

    def record_failure(
        self,
        row: KnowledgeGraphEntityMapping,
        *,
        stage: str,
        error_code: str,
        message: str,
        trace_id: str | None,
    ) -> None:
        row.sync_status = f"failed:{stage}"
        row.last_error_code = error_code
        row.last_error_message = message
        row.last_trace_id = trace_id
        self._session.flush()

    def mark_synced(
        self,
        row: KnowledgeGraphEntityMapping,
        *,
        remote_id: str,
        sync_version: str,
        trace_id: str | None,
    ) -> None:
        row.knowledge_graph_id = remote_id
        row.sync_version = sync_version
        row.sync_status = "synced"
        row.last_trace_id = trace_id
        row.synced_at = datetime.now(timezone.utc)
        row.last_error_code = None
        row.last_error_message = None
        self._session.flush()
