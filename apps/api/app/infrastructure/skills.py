from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.domain.skills import SkillCatalogConflict, normalize_skill_expression
from app.domain.json_types import freeze_json_object, thaw_json_object
from app.models.skill import Skill
from app.models.skill_alias import SkillAlias
from app.models.skill_catalog_version import SkillCatalogVersion
from app.models.skill_normalization_candidate import SkillNormalizationCandidate
from app.models.skill_taxonomy import SkillClassification, SkillTaxonomyNode
from app.models.user import utc_now
from app.contexts.catalog import (
    NormalizationCandidateRecord,
    SkillCatalogVersionRecord,
    SkillAliasRecord,
    SkillChanges,
    SkillClassificationRecord,
    SkillDraft,
    SkillRecord,
    SkillTaxonomyNodeDraft,
    SkillTaxonomyNodeChanges,
    SkillTaxonomyNodeRecord,
)


class SqlAlchemySkillRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, draft: SkillDraft) -> SkillRecord:
        row = Skill(
            skill_name=draft.skill_name, category=draft.category,
            description=draft.description, parent_skill_id=draft.parent_skill_id,
        )
        self._session.add(row)
        self._flush("Skill already exists")
        return self._skill(row)

    def get(self, skill_id: str) -> SkillRecord | None:
        row = self._session.get(Skill, skill_id)
        return self._skill(row) if row is not None else None

    def list_skills(self) -> list[SkillRecord]:
        rows = self._session.query(Skill).order_by(Skill.skill_name.asc()).all()
        return [self._skill(row) for row in rows]

    def update(self, skill_id: str, changes: SkillChanges) -> SkillRecord:
        row = self._session.get(Skill, skill_id)
        if row is None:
            raise LookupError(skill_id)
        for name in changes.changed_fields:
            setattr(row, name, getattr(changes, name))
        self._flush("Skill already exists")
        return self._skill(row)

    def delete(self, skill_id: str) -> None:
        self._session.query(SkillClassification).filter(
            SkillClassification.skill_id == skill_id
        ).delete()
        self._session.query(SkillAlias).filter(SkillAlias.skill_id == skill_id).delete()
        self._session.query(SkillNormalizationCandidate).filter(
            SkillNormalizationCandidate.candidate_skill_id == skill_id
        ).update({"candidate_skill_id": None})
        row = self._session.get(Skill, skill_id)
        if row is None:
            raise LookupError(skill_id)
        self._session.delete(row)

    def add_alias(self, skill_id: str, alias: str) -> SkillAliasRecord:
        row = SkillAlias(skill_id=skill_id, alias=alias)
        self._session.add(row)
        self._flush("Skill alias already exists")
        return self._alias(row)

    def list_aliases(self, skill_id: str | None = None) -> list[SkillAliasRecord]:
        query = self._session.query(SkillAlias)
        if skill_id is not None:
            query = query.filter(SkillAlias.skill_id == skill_id)
        rows = query.order_by(SkillAlias.alias.asc()).all()
        return [self._alias(row) for row in rows]

    def delete_alias(self, skill_id: str, alias_id: str) -> bool:
        row = (
            self._session.query(SkillAlias)
            .filter(SkillAlias.id == alias_id, SkillAlias.skill_id == skill_id)
            .first()
        )
        if row is None:
            return False
        self._session.delete(row)
        return True

    def add_candidate(
        self,
        raw_skill: str,
        context: str | None,
        source_type: str,
        evidence: str | None,
    ) -> NormalizationCandidateRecord:
        normalized_skill = normalize_skill_expression(raw_skill)
        now = utc_now()
        evidence_text = evidence or context
        row = (
            self._session.query(SkillNormalizationCandidate)
            .filter(
                SkillNormalizationCandidate.normalized_skill
                == normalized_skill
            )
            .one_or_none()
        )
        if row is not None:
            row.occurrence_count += 1
            row.last_seen_at = now
            if row.source_type == "unknown" and source_type != "unknown":
                row.source_type = source_type
            samples = list(row.evidence_samples or [])
            if evidence_text and not any(
                sample.get("source_type") == source_type
                and sample.get("evidence") == evidence_text
                for sample in samples
            ):
                samples.append(
                    {
                        "source_type": source_type,
                        "evidence": evidence_text,
                        "observed_at": now.isoformat(),
                    }
                )
                row.evidence_samples = samples
            self._session.flush()
            return self._candidate(row)

        samples = []
        if evidence_text:
            samples.append(
                {
                    "source_type": source_type,
                    "evidence": evidence_text,
                    "observed_at": now.isoformat(),
                }
            )
        row = SkillNormalizationCandidate(
            raw_skill=raw_skill,
            candidate_skill_id=None,
            confidence=0.0,
            context=evidence_text,
            occurrence_count=1,
            source_type=source_type,
            evidence_samples=samples,
            status="pending",
            first_seen_at=now,
            last_seen_at=now,
        )
        self._session.add(row)
        self._session.flush()
        return self._candidate(row)

    def get_candidate(self, candidate_id: str) -> NormalizationCandidateRecord | None:
        row = self._session.get(SkillNormalizationCandidate, candidate_id)
        return self._candidate(row) if row is not None else None

    def get_candidate_by_expression(
        self, normalized_skill: str
    ) -> NormalizationCandidateRecord | None:
        row = (
            self._session.query(SkillNormalizationCandidate)
            .filter(
                SkillNormalizationCandidate.normalized_skill
                == normalized_skill,
                SkillNormalizationCandidate.status.in_(
                    ("mapped_existing", "created_new")
                ),
                SkillNormalizationCandidate.candidate_skill_id.is_not(None),
            )
            .order_by(SkillNormalizationCandidate.reviewed_at.desc())
            .first()
        )
        return self._candidate(row) if row is not None else None

    def list_candidates(
        self,
        status: str | None = None,
        keyword: str | None = None,
        source_type: str | None = None,
    ) -> list[NormalizationCandidateRecord]:
        query = self._session.query(SkillNormalizationCandidate)
        if status is not None:
            query = query.filter(SkillNormalizationCandidate.status == status)
        if keyword:
            query = query.filter(
                SkillNormalizationCandidate.normalized_skill.contains(
                    normalize_skill_expression(keyword)
                )
            )
        rows = query.order_by(
            SkillNormalizationCandidate.last_seen_at.desc()
        ).all()
        if source_type is not None:
            rows = [
                row
                for row in rows
                if row.source_type == source_type
                or any(
                    sample.get("source_type") == source_type
                    for sample in (row.evidence_samples or [])
                )
            ]
        return [self._candidate(row) for row in rows]

    def set_candidate_status(
        self,
        candidate_id: str,
        status: str,
        skill_id: str | None,
        reviewer_id: str,
        reason: str | None,
    ) -> NormalizationCandidateRecord:
        row = self._session.get(SkillNormalizationCandidate, candidate_id)
        if row is None:
            raise LookupError(candidate_id)
        row.candidate_skill_id = skill_id
        row.status = status
        row.reviewer_id = reviewer_id
        row.reviewed_at = utc_now()
        row.decision_reason = reason
        if status in {"mapped_existing", "created_new"}:
            row.confidence = max(row.confidence, 0.9)
        self._session.flush()
        return self._candidate(row)

    def record_candidate_normalization(
        self,
        candidate_id: str,
        status: str,
        skill_id: str | None,
        catalog_version: str,
    ) -> NormalizationCandidateRecord:
        row = self._session.get(SkillNormalizationCandidate, candidate_id)
        if row is None:
            raise LookupError(candidate_id)
        row.status = status
        row.candidate_skill_id = skill_id
        row.normalization_catalog_version = catalog_version
        row.normalized_at = utc_now()
        if status in {"mapped_existing", "created_new"}:
            row.confidence = max(row.confidence, 0.9)
        self._session.flush()
        return self._candidate(row)

    def merge(self, source_skill_id: str, target_skill_id: str) -> None:
        row = self._session.get(Skill, source_skill_id)
        if row is None:
            raise LookupError(source_skill_id)
        row.status = "redirected"
        row.redirect_target_skill_id = target_skill_id
        self._session.flush()

    def latest_catalog_version(self) -> SkillCatalogVersionRecord | None:
        row = (
            self._session.query(SkillCatalogVersion)
            .order_by(SkillCatalogVersion.version_number.desc())
            .first()
        )
        return self._catalog_version(row) if row is not None else None

    def get_catalog_version(
        self, catalog_version: str
    ) -> SkillCatalogVersionRecord | None:
        row = (
            self._session.query(SkillCatalogVersion)
            .filter(SkillCatalogVersion.catalog_version == catalog_version)
            .one_or_none()
        )
        return self._catalog_version(row) if row is not None else None

    def add_catalog_version(
        self,
        version_number: int,
        catalog_version: str,
        snapshot: dict[str, object],
        change_summary: dict[str, object],
        published_by: str,
    ) -> SkillCatalogVersionRecord:
        row = SkillCatalogVersion(
            version_number=version_number,
            catalog_version=catalog_version,
            snapshot=thaw_json_object(snapshot),
            change_summary=thaw_json_object(change_summary),
            published_by=published_by,
        )
        self._session.add(row)
        self._flush("Skill catalog version already exists")
        return self._catalog_version(row)

    def add_taxonomy_node(
        self, draft: SkillTaxonomyNodeDraft
    ) -> SkillTaxonomyNodeRecord:
        row = SkillTaxonomyNode(
            facet=draft.facet,
            code=draft.code,
            name_zh=draft.name_zh,
            name_en=draft.name_en,
            parent_id=draft.parent_id,
            status=draft.status,
        )
        self._session.add(row)
        self._flush("Skill taxonomy node already exists")
        return self._taxonomy_node(row)

    def get_taxonomy_node(
        self, node_id: str
    ) -> SkillTaxonomyNodeRecord | None:
        row = self._session.get(SkillTaxonomyNode, node_id)
        return self._taxonomy_node(row) if row is not None else None

    def list_taxonomy_nodes(
        self, facet: str | None = None
    ) -> list[SkillTaxonomyNodeRecord]:
        query = self._session.query(SkillTaxonomyNode)
        if facet is not None:
            query = query.filter(SkillTaxonomyNode.facet == facet)
        rows = query.order_by(
            SkillTaxonomyNode.facet,
            SkillTaxonomyNode.code,
        ).all()
        return [self._taxonomy_node(row) for row in rows]

    def update_taxonomy_node(
        self,
        node_id: str,
        changes: SkillTaxonomyNodeChanges,
    ) -> SkillTaxonomyNodeRecord:
        row = self._session.get(SkillTaxonomyNode, node_id)
        if row is None:
            raise LookupError(node_id)
        for name in changes.changed_fields:
            setattr(row, name, getattr(changes, name))
        self._flush("Skill taxonomy node update conflicts")
        return self._taxonomy_node(row)

    def add_classification(
        self,
        skill_id: str,
        node: SkillTaxonomyNodeRecord,
        is_primary: bool,
    ) -> SkillClassificationRecord:
        row = SkillClassification(
            skill_id=skill_id,
            taxonomy_node_id=node.node_id,
            facet=node.facet,
            is_primary=is_primary,
        )
        self._session.add(row)
        self._flush("Skill classification conflicts with existing relation")
        return SkillClassificationRecord(
            row.id,
            row.skill_id,
            row.taxonomy_node_id,
            node.facet,
            node.code,
            node.name_zh,
            node.name_en,
            row.is_primary,
            row.created_at,
        )

    def get_classification(
        self, classification_id: str
    ) -> SkillClassificationRecord | None:
        row = self._session.get(SkillClassification, classification_id)
        return self._classification(row) if row is not None else None

    def list_classifications(
        self, skill_id: str
    ) -> list[SkillClassificationRecord]:
        rows = (
            self._session.query(SkillClassification)
            .filter(SkillClassification.skill_id == skill_id)
            .order_by(
                SkillClassification.facet,
                SkillClassification.is_primary.desc(),
                SkillClassification.created_at,
            )
            .all()
        )
        return [self._classification(row) for row in rows]

    def list_domain_classifications(self) -> list[tuple[str, str]]:
        rows = (
            self._session.query(
                SkillClassification.skill_id,
                SkillTaxonomyNode.name_zh,
            )
            .join(
                SkillTaxonomyNode,
                SkillTaxonomyNode.id == SkillClassification.taxonomy_node_id,
            )
            .filter(
                SkillClassification.facet == "domain",
                SkillTaxonomyNode.status == "active",
            )
            .order_by(
                SkillClassification.skill_id,
                SkillClassification.is_primary.desc(),
            )
            .all()
        )
        return [(skill_id, name_zh) for skill_id, name_zh in rows]

    def delete_classification(
        self, skill_id: str, classification_id: str
    ) -> bool:
        row = (
            self._session.query(SkillClassification)
            .filter(
                SkillClassification.id == classification_id,
                SkillClassification.skill_id == skill_id,
            )
            .first()
        )
        if row is None:
            return False
        self._session.delete(row)
        return True

    def _candidate(self, row: SkillNormalizationCandidate) -> NormalizationCandidateRecord:
        skill = self._session.get(Skill, row.candidate_skill_id) if row.candidate_skill_id else None
        return NormalizationCandidateRecord(
            candidate_id=row.id,
            raw_skill=row.raw_skill,
            normalized_skill=row.normalized_skill,
            candidate_skill_id=row.candidate_skill_id,
            candidate_skill_name=skill.skill_name if skill else None,
            confidence=row.confidence,
            context=row.context,
            occurrence_count=row.occurrence_count,
            source_type=row.source_type,
            evidence_samples=tuple(row.evidence_samples or []),
            status=row.status,
            first_seen_at=row.first_seen_at,
            last_seen_at=row.last_seen_at,
            reviewer_id=row.reviewer_id,
            reviewed_at=row.reviewed_at,
            decision_reason=row.decision_reason,
            normalization_catalog_version=row.normalization_catalog_version,
            normalized_at=row.normalized_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _taxonomy_node(row: SkillTaxonomyNode) -> SkillTaxonomyNodeRecord:
        return SkillTaxonomyNodeRecord(
            row.id,
            row.facet,
            row.code,
            row.name_zh,
            row.name_en,
            row.parent_id,
            row.status,
            row.created_at,
            row.updated_at,
        )

    def _classification(
        self, row: SkillClassification
    ) -> SkillClassificationRecord:
        node = self._session.get(SkillTaxonomyNode, row.taxonomy_node_id)
        if node is None:
            raise RuntimeError(
                "Skill classification references a missing taxonomy node"
            )
        return SkillClassificationRecord(
            row.id,
            row.skill_id,
            row.taxonomy_node_id,
            row.facet,
            node.code,
            node.name_zh,
            node.name_en,
            row.is_primary,
            row.created_at,
        )

    def _flush(self, message: str) -> None:
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise SkillCatalogConflict(message) from exc

    @staticmethod
    def _skill(row: Skill) -> SkillRecord:
        return SkillRecord(
            skill_id=row.id,
            skill_name=row.skill_name,
            catalog_code=row.catalog_code,
            category=row.category,
            description=row.description,
            parent_skill_id=row.parent_skill_id,
            status=row.status,
            redirect_target_skill_id=row.redirect_target_skill_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _catalog_version(row: SkillCatalogVersion) -> SkillCatalogVersionRecord:
        return SkillCatalogVersionRecord(
            row.id,
            row.version_number,
            row.catalog_version,
            freeze_json_object(row.snapshot, field="skill_catalog.snapshot"),
            freeze_json_object(
                row.change_summary, field="skill_catalog.change_summary"
            ),
            row.published_by,
            row.published_at,
        )

    @staticmethod
    def _alias(row: SkillAlias) -> SkillAliasRecord:
        return SkillAliasRecord(row.id, row.skill_id, row.alias)


class SqlAlchemySkillUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    def __enter__(self) -> "SqlAlchemySkillUnitOfWork":
        self._session = self._session_factory()
        self.skills = SqlAlchemySkillRepository(self._session)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._session is None:
            return
        if exc_type is not None:
            self.rollback()
        self._session.close()

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("unit of work has not been entered")
        self._session.commit()

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()
