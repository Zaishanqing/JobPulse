from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.domain.accounts import AccountActor
from app.domain.json_types import freeze_json_object
from app.domain.skills import (
    SkillCatalogConflict,
    SkillRuleViolation,
    clean_skill_expression,
    find_text_match,
    normalize_skill_expression,
    require_skill_admin,
    validate_taxonomy_facet,
    validate_taxonomy_node,
)
from app.contexts.catalog._ports.skills import (
    DownstreamSkillProjection,
    MergeResult,
    MergePreview,
    MergeSkillSummary,
    CatalogDraftPreview,
    NormalizationCandidateRecord,
    NormalizationResult,
    NormalizedSkillCandidate,
    RenormalizationSummary,
    SkillAliasRecord,
    SkillChanges,
    SkillClassificationRecord,
    SkillCatalogVersionRecord,
    SkillDraft,
    SkillRecord,
    SkillTaxonomyNodeDraft,
    SkillTaxonomyNodeChanges,
    SkillTaxonomyNodeRecord,
    SkillUnitOfWork,
)
from app.contexts.catalog._ports.normalization_suggestions import (
    CatalogEmbeddingPort,
    NormalizationSuggestion,
)
from app.contexts.catalog._applications.normalization_suggestions import (
    rank_normalization_suggestions,
)


class SkillNotFound(LookupError):
    pass


class SkillAliasNotFound(LookupError):
    pass


class SkillTaxonomyNodeNotFound(LookupError):
    pass


class SkillClassificationNotFound(LookupError):
    pass


class NormalizationCandidateNotFound(LookupError):
    pass


class SkillCatalogVersionNotFound(LookupError):
    pass


@dataclass(frozen=True)
class ManageSkills:
    uow_factory: Callable[[], SkillUnitOfWork]
    normalization_embedding: CatalogEmbeddingPort | None = None

    def create(self, actor: AccountActor, draft: SkillDraft, aliases: tuple[str, ...]) -> SkillRecord:
        require_skill_admin(actor.role)
        with self.uow_factory() as uow:
            record = uow.skills.add(draft)
            for alias in aliases:
                uow.skills.add_alias(record.skill_id, alias)
            uow.commit()
            return record

    def list(self) -> list[SkillRecord]:
        with self.uow_factory() as uow:
            return uow.skills.list_skills()

    def create_taxonomy_node(
        self,
        actor: AccountActor,
        draft: SkillTaxonomyNodeDraft,
    ) -> SkillTaxonomyNodeRecord:
        require_skill_admin(actor.role)
        validate_taxonomy_node(
            draft.facet,
            draft.code,
            draft.name_zh,
            draft.status,
        )
        with self.uow_factory() as uow:
            if draft.parent_id is not None:
                parent = uow.skills.get_taxonomy_node(draft.parent_id)
                if parent is None:
                    raise SkillTaxonomyNodeNotFound("Taxonomy parent not found")
                if parent.facet != draft.facet:
                    raise SkillCatalogConflict(
                        "Taxonomy parent must use the same facet"
                    )
                if parent.status != "active":
                    raise SkillCatalogConflict(
                        "Inactive taxonomy parent cannot receive children"
                    )
            record = uow.skills.add_taxonomy_node(draft)
            uow.commit()
            return record

    def list_taxonomy_nodes(
        self, facet: str | None = None
    ) -> list[SkillTaxonomyNodeRecord]:
        if facet is not None:
            validate_taxonomy_facet(facet)
        with self.uow_factory() as uow:
            return uow.skills.list_taxonomy_nodes(facet)

    def update_taxonomy_node(
        self,
        actor: AccountActor,
        node_id: str,
        changes: SkillTaxonomyNodeChanges,
    ) -> SkillTaxonomyNodeRecord:
        require_skill_admin(actor.role)
        with self.uow_factory() as uow:
            current = uow.skills.get_taxonomy_node(node_id)
            if current is None:
                raise SkillTaxonomyNodeNotFound("Taxonomy node not found")
            name_zh = (
                changes.name_zh
                if "name_zh" in changes.changed_fields
                else current.name_zh
            )
            status = (
                changes.status
                if "status" in changes.changed_fields
                else current.status
            )
            validate_taxonomy_node(
                current.facet,
                current.code,
                name_zh or "",
                status or "",
            )
            if "parent_id" in changes.changed_fields:
                if changes.parent_id == node_id:
                    raise SkillCatalogConflict(
                        "Taxonomy node cannot be its own parent"
                    )
                if changes.parent_id is not None:
                    parent = uow.skills.get_taxonomy_node(changes.parent_id)
                    if parent is None:
                        raise SkillTaxonomyNodeNotFound(
                            "Taxonomy parent not found"
                        )
                    if parent.facet != current.facet:
                        raise SkillCatalogConflict(
                            "Taxonomy parent must use the same facet"
                        )
                    if parent.status != "active":
                        raise SkillCatalogConflict(
                            "Inactive taxonomy parent cannot receive children"
                        )
                    ancestor = parent
                    while ancestor.parent_id is not None:
                        if ancestor.parent_id == node_id:
                            raise SkillCatalogConflict(
                                "Taxonomy hierarchy cannot contain a cycle"
                            )
                        next_ancestor = uow.skills.get_taxonomy_node(
                            ancestor.parent_id
                        )
                        if next_ancestor is None:
                            raise SkillCatalogConflict(
                                "Taxonomy hierarchy references a missing parent"
                            )
                        ancestor = next_ancestor
            record = uow.skills.update_taxonomy_node(node_id, changes)
            uow.commit()
            return record

    def classify(
        self,
        actor: AccountActor,
        skill_id: str,
        taxonomy_node_id: str,
        is_primary: bool,
    ) -> SkillClassificationRecord:
        require_skill_admin(actor.role)
        with self.uow_factory() as uow:
            if uow.skills.get(skill_id) is None:
                raise SkillNotFound("Skill not found")
            node = uow.skills.get_taxonomy_node(taxonomy_node_id)
            if node is None:
                raise SkillTaxonomyNodeNotFound("Taxonomy node not found")
            if node.status != "active":
                raise SkillCatalogConflict(
                    "Inactive taxonomy node cannot be assigned"
                )
            existing = uow.skills.list_classifications(skill_id)
            if node.facet in {"concept_class", "technology_kind"}:
                if not is_primary:
                    raise SkillCatalogConflict(
                        f"{node.facet} classification must be primary"
                    )
                if any(item.facet == node.facet for item in existing):
                    raise SkillCatalogConflict(
                        f"Skill already has a {node.facet} classification"
                    )
            if is_primary and any(
                item.facet == node.facet and item.is_primary
                for item in existing
            ):
                raise SkillCatalogConflict(
                    f"Skill already has a primary {node.facet} classification"
                )
            if node.facet == "technology_kind":
                concept = next(
                    (
                        item
                        for item in existing
                        if item.facet == "concept_class"
                    ),
                    None,
                )
                if concept is None or concept.code != "technology":
                    raise SkillCatalogConflict(
                        "Technology kind requires concept_class=technology"
                    )
            if node.facet == "concept_class" and node.code != "technology":
                if any(item.facet == "technology_kind" for item in existing):
                    raise SkillCatalogConflict(
                        "Non-technology concept cannot retain a technology kind"
                    )
            record = uow.skills.add_classification(
                skill_id,
                node,
                is_primary,
            )
            uow.commit()
            return record

    def list_classifications(
        self, skill_id: str
    ) -> list[SkillClassificationRecord]:
        self.get(skill_id)
        with self.uow_factory() as uow:
            return uow.skills.list_classifications(skill_id)

    def remove_classification(
        self,
        actor: AccountActor,
        skill_id: str,
        classification_id: str,
    ) -> None:
        require_skill_admin(actor.role)
        self.get(skill_id)
        with self.uow_factory() as uow:
            classification = uow.skills.get_classification(classification_id)
            if classification is None or classification.skill_id != skill_id:
                raise SkillClassificationNotFound(
                    "Skill classification not found"
                )
            if (
                classification.facet == "concept_class"
                and classification.code == "technology"
                and any(
                    item.facet == "technology_kind"
                    for item in uow.skills.list_classifications(skill_id)
                )
            ):
                raise SkillCatalogConflict(
                    "Remove technology kind before technology concept class"
                )
            if not uow.skills.delete_classification(
                skill_id,
                classification_id,
            ):
                raise SkillClassificationNotFound(
                    "Skill classification not found"
                )
            uow.commit()

    def get(self, skill_id: str) -> SkillRecord:
        with self.uow_factory() as uow:
            record = uow.skills.get(skill_id)
        if record is None:
            raise SkillNotFound("Skill not found")
        return record

    def update(self, actor: AccountActor, skill_id: str, changes: SkillChanges) -> SkillRecord:
        require_skill_admin(actor.role)
        self.get(skill_id)
        with self.uow_factory() as uow:
            record = uow.skills.update(skill_id, changes)
            uow.commit()
            return record

    def delete(self, actor: AccountActor, skill_id: str) -> None:
        require_skill_admin(actor.role)
        self.get(skill_id)
        with self.uow_factory() as uow:
            uow.skills.delete(skill_id)
            uow.commit()

    def add_alias(self, actor: AccountActor, skill_id: str, alias: str) -> SkillAliasRecord:
        require_skill_admin(actor.role)
        self.get(skill_id)
        with self.uow_factory() as uow:
            record = uow.skills.add_alias(skill_id, alias)
            uow.commit()
            return record

    def list_aliases(self, skill_id: str) -> list[SkillAliasRecord]:
        self.get(skill_id)
        with self.uow_factory() as uow:
            return uow.skills.list_aliases(skill_id)

    def suggest_normalizations(
        self,
        actor: AccountActor,
        raw_skill: str,
        context: str | None,
        top_k: int,
    ) -> tuple[NormalizationSuggestion, ...]:
        """Rank reviewer suggestions without mutating Catalog or candidate state."""
        require_skill_admin(actor.role)
        normalized_expression = normalize_skill_expression(raw_skill)
        if not normalized_expression:
            raise SkillRuleViolation("Skill expression is required")
        if not 1 <= top_k <= 20:
            raise SkillRuleViolation("top_k must be between 1 and 20")
        with self.uow_factory() as uow:
            reviewed = uow.skills.get_candidate_by_expression(normalized_expression)
            reviewed_skill_id = (
                reviewed.candidate_skill_id
                if reviewed is not None and reviewed.candidate_skill_id is not None
                else None
            )
            return rank_normalization_suggestions(
                raw_skill=raw_skill,
                context=context,
                skills=uow.skills.list_skills(),
                aliases=uow.skills.list_aliases(),
                reviewed_skill_id=reviewed_skill_id,
                top_k=top_k,
                embedding=self.normalization_embedding,
            )

    def delete_alias(self, actor: AccountActor, skill_id: str, alias_id: str) -> None:
        require_skill_admin(actor.role)
        self.get(skill_id)
        with self.uow_factory() as uow:
            if not uow.skills.delete_alias(skill_id, alias_id):
                raise SkillAliasNotFound("Skill alias not found")
            uow.commit()

    def normalize(
        self,
        actor: AccountActor,
        raw_skill: str,
        context: str | None,
        source_type: str = "unknown",
        evidence: str | None = None,
    ) -> NormalizationResult:
        require_skill_admin(actor.role)
        normalized_expression = normalize_skill_expression(raw_skill)
        if not normalized_expression:
            raise SkillRuleViolation("Skill expression is required")
        with self.uow_factory() as uow:
            reviewed = uow.skills.get_candidate_by_expression(
                normalized_expression
            )
            if reviewed is not None and reviewed.candidate_skill_id is not None:
                reviewed_skill = uow.skills.get(reviewed.candidate_skill_id)
                if reviewed_skill is not None:
                    return NormalizationResult(
                        raw_skill,
                        (self._resolved_normalized(uow, reviewed_skill, reviewed.confidence),),
                        False,
                        None,
                    )
            skills = uow.skills.list_skills()
            aliases = uow.skills.list_aliases()
            match = find_text_match(
                raw_skill,
                tuple((item.skill_id, item.skill_name) for item in skills),
                tuple((item.skill_id, item.alias) for item in aliases),
            )
            if match is None or match.confidence < 0.9:
                candidate = uow.skills.add_candidate(
                    clean_skill_expression(raw_skill),
                    context,
                    source_type,
                    evidence,
                )
                uow.commit()
                suggestions: tuple[NormalizedSkillCandidate, ...] = ()
                if match is not None:
                    skill = next(
                        item for item in skills if item.skill_id == match.skill_id
                    )
                    suggestions = (
                        self._resolved_normalized(uow, skill, match.confidence),
                    )
                return NormalizationResult(
                    raw_skill,
                    suggestions,
                    True,
                    candidate.candidate_id,
                )
            skill = next(item for item in skills if item.skill_id == match.skill_id)
            normalized = self._resolved_normalized(uow, skill, match.confidence)
            return NormalizationResult(raw_skill, (normalized,), False, None)

    def normalize_batch(
        self,
        actor: AccountActor,
        items: tuple[tuple[str, str | None, str, str | None], ...],
    ) -> list[NormalizationResult]:
        require_skill_admin(actor.role)
        return [
            self.normalize(actor, raw, context, source_type, evidence)
            for raw, context, source_type, evidence in items
        ]

    def list_candidates(
        self,
        actor: AccountActor,
        status: str | None = None,
        keyword: str | None = None,
        source_type: str | None = None,
    ) -> list[NormalizationCandidateRecord]:
        require_skill_admin(actor.role)
        with self.uow_factory() as uow:
            return uow.skills.list_candidates(status, keyword, source_type)

    def map_existing_candidate(
        self,
        actor: AccountActor,
        candidate_id: str,
        skill_id: str,
        add_alias: bool,
        reason: str,
    ) -> NormalizationCandidateRecord:
        require_skill_admin(actor.role)
        self._require_review_reason(reason)
        with self.uow_factory() as uow:
            candidate = self._reviewable_candidate(uow, candidate_id)
            if uow.skills.get(skill_id) is None:
                raise SkillNotFound("Skill not found")
            if add_alias:
                uow.skills.add_alias(skill_id, candidate.raw_skill)
            record = uow.skills.set_candidate_status(
                candidate_id,
                "mapped_existing",
                skill_id,
                actor.account_id,
                reason,
            )
            uow.commit()
            return record

    def create_new_candidate(
        self,
        actor: AccountActor,
        candidate_id: str,
        draft: SkillDraft,
        concept_class_id: str,
        technology_kind_id: str | None,
        domain_id: str,
        add_alias: bool,
        reason: str,
    ) -> NormalizationCandidateRecord:
        require_skill_admin(actor.role)
        self._require_review_reason(reason)
        if not clean_skill_expression(draft.skill_name):
            raise SkillRuleViolation("Skill name is required")
        with self.uow_factory() as uow:
            candidate = self._reviewable_candidate(uow, candidate_id)
            concept = uow.skills.get_taxonomy_node(concept_class_id)
            domain = uow.skills.get_taxonomy_node(domain_id)
            technology_kind = (
                uow.skills.get_taxonomy_node(technology_kind_id)
                if technology_kind_id is not None
                else None
            )
            if concept is None or domain is None or (
                technology_kind_id is not None and technology_kind is None
            ):
                raise SkillTaxonomyNodeNotFound("Skill taxonomy node not found")
            if concept.facet != "concept_class":
                raise SkillCatalogConflict("concept_class_id must reference concept_class")
            if domain.facet != "domain":
                raise SkillCatalogConflict("domain_id must reference domain")
            if concept.status != "active" or domain.status != "active":
                raise SkillCatalogConflict("Inactive taxonomy node cannot be assigned")
            if concept.code == "technology":
                if technology_kind is None:
                    raise SkillCatalogConflict(
                        "technology_kind_id is required for technology skills"
                    )
                if technology_kind.facet != "technology_kind":
                    raise SkillCatalogConflict(
                        "technology_kind_id must reference technology_kind"
                    )
                if technology_kind.status != "active":
                    raise SkillCatalogConflict(
                        "Inactive taxonomy node cannot be assigned"
                    )
            elif technology_kind is not None:
                raise SkillCatalogConflict(
                    "technology_kind is only valid for technology skills"
                )

            created = uow.skills.add(draft)
            uow.skills.add_classification(created.skill_id, concept, True)
            if technology_kind is not None:
                uow.skills.add_classification(
                    created.skill_id,
                    technology_kind,
                    True,
                )
            uow.skills.add_classification(created.skill_id, domain, True)
            if add_alias:
                uow.skills.add_alias(created.skill_id, candidate.raw_skill)
            record = uow.skills.set_candidate_status(
                candidate_id,
                "created_new",
                created.skill_id,
                actor.account_id,
                reason,
            )
            uow.commit()
            return record

    def exclude_non_skill_candidate(
        self,
        actor: AccountActor,
        candidate_id: str,
        reason: str,
    ) -> NormalizationCandidateRecord:
        return self._finish_without_skill(
            actor,
            candidate_id,
            "excluded_non_skill",
            reason,
        )

    def defer_candidate(
        self,
        actor: AccountActor,
        candidate_id: str,
        reason: str,
    ) -> NormalizationCandidateRecord:
        return self._finish_without_skill(
            actor,
            candidate_id,
            "deferred",
            reason,
        )

    def _finish_without_skill(
        self,
        actor: AccountActor,
        candidate_id: str,
        status: str,
        reason: str,
    ) -> NormalizationCandidateRecord:
        require_skill_admin(actor.role)
        self._require_review_reason(reason)
        with self.uow_factory() as uow:
            candidate = self._reviewable_candidate(uow, candidate_id)
            if status == "deferred" and candidate.status == "deferred":
                raise SkillCatalogConflict("Normalization candidate is already deferred")
            record = uow.skills.set_candidate_status(
                candidate_id,
                status,
                None,
                actor.account_id,
                reason,
            )
            uow.commit()
            return record

    def confirm_candidate(
        self,
        actor: AccountActor,
        candidate_id: str,
        skill_id: str | None,
        reason: str | None = None,
    ) -> NormalizationCandidateRecord:
        if skill_id is None:
            raise SkillRuleViolation("map-existing requires skill_id")
        return self.map_existing_candidate(
            actor,
            candidate_id,
            skill_id,
            False,
            reason or "",
        )

    def reject_candidate(
        self,
        actor: AccountActor,
        candidate_id: str,
        reason: str | None = None,
    ) -> NormalizationCandidateRecord:
        return self.exclude_non_skill_candidate(
            actor,
            candidate_id,
            reason or "",
        )

    def preview_merge(
        self,
        actor: AccountActor,
        source_id: str,
        target_id: str,
    ) -> MergePreview:
        require_skill_admin(actor.role)
        with self.uow_factory() as uow:
            source, target = self._validate_merge(uow, source_id, target_id)
            source_aliases = uow.skills.list_aliases(source_id)
            target_aliases = uow.skills.list_aliases(target_id)
            source_classifications = tuple(
                uow.skills.list_classifications(source_id)
            )
            target_classifications = tuple(
                uow.skills.list_classifications(target_id)
            )
            candidates = uow.skills.list_candidates()
            source_candidates = tuple(
                item
                for item in candidates
                if item.candidate_skill_id == source_id
            )
            target_candidate_count = sum(
                item.candidate_skill_id == target_id for item in candidates
            )
            impact_by_source = {
                source_type: sum(
                    source_type
                    in {
                        item.source_type,
                        *(
                            sample.get("source_type", "unknown")
                            for sample in item.evidence_samples
                        ),
                    }
                    for item in source_candidates
                )
                for source_type in ("jd", "cv", "manual", "unknown")
            }
            return MergePreview(
                source=MergeSkillSummary(
                    source,
                    len(source_aliases),
                    source_classifications,
                    len(source_candidates),
                ),
                target=MergeSkillSummary(
                    target,
                    len(target_aliases),
                    target_classifications,
                    target_candidate_count,
                ),
                impact_by_source=freeze_json_object(impact_by_source),
                classification_conflicts=self._classification_conflicts(
                    source_classifications,
                    target_classifications,
                ),
            )

    def merge(self, actor: AccountActor, source_id: str, target_id: str) -> MergeResult:
        require_skill_admin(actor.role)
        with self.uow_factory() as uow:
            _, target = self._validate_merge(uow, source_id, target_id)
            uow.skills.merge(source_id, target_id)
            uow.commit()
        return MergeResult(source_id, target_id, target.skill_name, "redirected")

    def preview_catalog_draft(self, actor: AccountActor) -> CatalogDraftPreview:
        require_skill_admin(actor.role)
        with self.uow_factory() as uow:
            snapshot = self._catalog_snapshot(uow)
            latest = uow.skills.latest_catalog_version()
            baseline = latest.snapshot if latest is not None else None
            return CatalogDraftPreview(
                based_on_catalog_version=(
                    latest.catalog_version if latest is not None else None
                ),
                change_summary=freeze_json_object(
                    self._catalog_change_summary(baseline, snapshot)
                ),
                validation_issues=self._catalog_validation_issues(uow, snapshot),
            )

    def publish_catalog(
        self,
        actor: AccountActor,
    ) -> SkillCatalogVersionRecord:
        require_skill_admin(actor.role)
        with self.uow_factory() as uow:
            snapshot = self._catalog_snapshot(uow)
            issues = self._catalog_validation_issues(uow, snapshot)
            if issues:
                raise SkillCatalogConflict("; ".join(issues))
            latest = uow.skills.latest_catalog_version()
            baseline = latest.snapshot if latest is not None else None
            version_number = (
                latest.version_number + 1 if latest is not None else 1
            )
            catalog_version = f"skill-catalog.v{version_number}"
            published_snapshot = {
                **snapshot,
                "catalog_version": catalog_version,
            }
            record = uow.skills.add_catalog_version(
                version_number,
                catalog_version,
                freeze_json_object(published_snapshot),
                freeze_json_object(
                    self._catalog_change_summary(baseline, snapshot)
                ),
                actor.account_id,
            )
            uow.commit()
            return record

    def latest_published_catalog(self) -> SkillCatalogVersionRecord:
        with self.uow_factory() as uow:
            record = uow.skills.latest_catalog_version()
        if record is None:
            raise SkillCatalogVersionNotFound(
                "Published skill catalog version not found"
            )
        return record

    def get_published_catalog(
        self,
        catalog_version: str,
    ) -> SkillCatalogVersionRecord:
        with self.uow_factory() as uow:
            record = uow.skills.get_catalog_version(catalog_version)
        if record is None:
            raise SkillCatalogVersionNotFound(
                "Published skill catalog version not found"
            )
        return record

    def renormalize_candidates(
        self,
        actor: AccountActor,
    ) -> RenormalizationSummary:
        require_skill_admin(actor.role)
        with self.uow_factory() as uow:
            published = uow.skills.latest_catalog_version()
            if published is None:
                raise SkillCatalogVersionNotFound(
                    "Published skill catalog version not found"
                )
            lookup, resolved_ids = self._published_skill_lookup(
                published.snapshot
            )
            candidates = uow.skills.list_candidates()
            resolved_count = 0
            touched = []
            for candidate in candidates:
                status = candidate.status
                skill_id = candidate.candidate_skill_id
                if status in {"pending", "deferred"}:
                    matched_id = lookup.get(candidate.normalized_skill)
                    if matched_id is not None:
                        status = "mapped_existing"
                        skill_id = matched_id
                        resolved_count += 1
                elif status in {"mapped_existing", "created_new"}:
                    skill_id = resolved_ids.get(skill_id)
                    if skill_id is None:
                        status = "pending"
                updated = uow.skills.record_candidate_normalization(
                    candidate.candidate_id,
                    status,
                    skill_id,
                    published.catalog_version,
                )
                touched.append(updated)
            uow.commit()
        return RenormalizationSummary(
            catalog_version=published.catalog_version,
            resolved_candidate_count=resolved_count,
            unresolved_candidate_count=sum(
                item.status in {"pending", "deferred"} for item in touched
            ),
            excluded_non_skill_count=sum(
                item.status == "excluded_non_skill" for item in touched
            ),
            affected_jd_count=self._source_impact_count(touched, "jd"),
            affected_cv_count=self._source_impact_count(touched, "cv"),
        )

    def downstream_skill_projection(self) -> DownstreamSkillProjection:
        with self.uow_factory() as uow:
            published = uow.skills.latest_catalog_version()
            if published is None:
                raise SkillCatalogVersionNotFound(
                    "Published skill catalog version not found"
                )
            _, resolved_ids = self._published_skill_lookup(published.snapshot)
            candidates = uow.skills.list_candidates()
        official_ids = {
            resolved_ids[item.candidate_skill_id]
            for item in candidates
            if item.status in {"mapped_existing", "created_new"}
            and item.candidate_skill_id in resolved_ids
        }
        return DownstreamSkillProjection(
            catalog_version=published.catalog_version,
            resolved_skill_ids=tuple(sorted(official_ids)),
            unresolved_candidates=tuple(
                item
                for item in candidates
                if item.status in {"pending", "deferred"}
            ),
        )

    def category_tree(self) -> list[tuple[str, list[SkillRecord]]]:
        categories: dict[str, list[SkillRecord]] = {}
        for skill in self.list():
            categories.setdefault(skill.category or "未分类", []).append(skill)
        return sorted(categories.items())

    def domain_tree(self) -> list[tuple[str, list[SkillRecord]]]:
        skills = {item.skill_id: item for item in self.list()}
        categories: dict[str, list[SkillRecord]] = {}
        assigned: set[str] = set()
        with self.uow_factory() as uow:
            for skill_id, domain_name in uow.skills.list_domain_classifications():
                if skill_id in assigned or skill_id not in skills:
                    continue
                assigned.add(skill_id)
                categories.setdefault(domain_name, []).append(skills[skill_id])
        uncategorized = [skills[item] for item in skills if item not in assigned]
        if uncategorized:
            categories["未分类"] = uncategorized
        return sorted(categories.items())

    @staticmethod
    def _source_impact_count(
        candidates: list[NormalizationCandidateRecord],
        source_type: str,
    ) -> int:
        return sum(
            source_type
            in {
                item.source_type,
                *(sample.get("source_type", "unknown") for sample in item.evidence_samples),
            }
            for item in candidates
        )

    @staticmethod
    def _published_skill_lookup(
        snapshot: dict[str, object],
    ) -> tuple[dict[str, str], dict[str, str]]:
        skills = {
            item["skill_id"]: item
            for item in snapshot.get("skills", [])
        }

        def resolve(skill_id: str) -> str | None:
            skill = skills.get(skill_id)
            if skill is None:
                return None
            if skill.get("status") == "active":
                return skill_id
            if skill.get("status") == "redirected":
                target_id = skill.get("redirect_target_skill_id")
                target = skills.get(target_id)
                if target is not None and target.get("status") == "active":
                    return target_id
            return None

        resolved_ids = {
            skill_id: resolved
            for skill_id in skills
            if (resolved := resolve(skill_id)) is not None
        }
        lookup = {
            normalize_skill_expression(item["skill_name"]): resolved_ids[item["skill_id"]]
            for item in skills.values()
            if item["skill_id"] in resolved_ids
        }
        for alias in snapshot.get("aliases", []):
            resolved = resolved_ids.get(alias["skill_id"])
            if resolved is not None:
                lookup[normalize_skill_expression(alias["alias"])] = resolved
        return lookup, resolved_ids

    def _get_candidate(self, candidate_id: str) -> NormalizationCandidateRecord:
        with self.uow_factory() as uow:
            record = uow.skills.get_candidate(candidate_id)
        if record is None:
            raise NormalizationCandidateNotFound("Normalization candidate not found")
        return record

    @staticmethod
    def _require_review_reason(reason: str) -> None:
        if not reason.strip():
            raise SkillRuleViolation("Review decision reason is required")

    @staticmethod
    def _reviewable_candidate(
        uow: SkillUnitOfWork,
        candidate_id: str,
    ) -> NormalizationCandidateRecord:
        candidate = uow.skills.get_candidate(candidate_id)
        if candidate is None:
            raise NormalizationCandidateNotFound(
                "Normalization candidate not found"
            )
        if candidate.status not in {"pending", "deferred"}:
            raise SkillCatalogConflict("Normalization candidate is already processed")
        return candidate

    @staticmethod
    def _validate_merge(
        uow: SkillUnitOfWork,
        source_id: str,
        target_id: str,
    ) -> tuple[SkillRecord, SkillRecord]:
        source = uow.skills.get(source_id)
        target = uow.skills.get(target_id)
        if source is None or target is None:
            raise SkillNotFound("Skill not found")
        if source_id == target_id:
            raise SkillCatalogConflict("Source and target skills must be different")
        if target.status != "active":
            raise SkillCatalogConflict("Target skill must be active")
        return source, target

    @staticmethod
    def _classification_conflicts(
        source: tuple[SkillClassificationRecord, ...],
        target: tuple[SkillClassificationRecord, ...],
    ) -> tuple[str, ...]:
        conflicts = []
        for facet in ("concept_class", "technology_kind"):
            source_item = next((item for item in source if item.facet == facet), None)
            target_item = next((item for item in target if item.facet == facet), None)
            if source_item and target_item and source_item.code != target_item.code:
                conflicts.append(
                    f"{facet}: {source_item.code} -> {target_item.code}"
                )
        source_domain = next(
            (item for item in source if item.facet == "domain" and item.is_primary),
            None,
        )
        target_domain = next(
            (item for item in target if item.facet == "domain" and item.is_primary),
            None,
        )
        if source_domain and target_domain and source_domain.code != target_domain.code:
            conflicts.append(
                f"domain: {source_domain.code} -> {target_domain.code}"
            )
        return tuple(conflicts)

    @staticmethod
    def _resolved_normalized(
        uow: SkillUnitOfWork,
        matched: SkillRecord,
        confidence: float,
    ) -> NormalizedSkillCandidate:
        if matched.status == "redirected" and matched.redirect_target_skill_id:
            target = uow.skills.get(matched.redirect_target_skill_id)
            if target is not None and target.status == "active":
                return NormalizedSkillCandidate(
                    target.skill_id,
                    target.skill_name,
                    target.category,
                    confidence,
                    matched.skill_id,
                    matched.skill_name,
                )
        return NormalizedSkillCandidate(
            matched.skill_id,
            matched.skill_name,
            matched.category,
            confidence,
        )

    @staticmethod
    def _catalog_snapshot(uow: SkillUnitOfWork) -> dict[str, object]:
        skills = sorted(uow.skills.list_skills(), key=lambda item: item.skill_id)
        aliases = sorted(
            uow.skills.list_aliases(),
            key=lambda item: (item.skill_id, item.alias, item.alias_id),
        )
        classifications = sorted(
            (
                row
                for skill in skills
                for row in uow.skills.list_classifications(skill.skill_id)
            ),
            key=lambda item: (
                item.skill_id,
                item.facet,
                item.code,
                item.classification_id,
            ),
        )
        skill_rows = [
            {
                "skill_id": item.skill_id,
                "catalog_code": item.catalog_code,
                "skill_name": item.skill_name,
                "category": item.category,
                "description": item.description,
                "parent_skill_id": item.parent_skill_id,
                "status": item.status,
                "redirect_target_skill_id": item.redirect_target_skill_id,
            }
            for item in skills
        ]
        return {
            "schema": "skill-catalog-snapshot.v1",
            "taxonomy_catalog_version": "skill-taxonomy-catalog.v1",
            "skills": skill_rows,
            "aliases": [
                {
                    "alias_id": item.alias_id,
                    "skill_id": item.skill_id,
                    "alias": item.alias,
                }
                for item in aliases
            ],
            "classifications": [
                {
                    "classification_id": item.classification_id,
                    "skill_id": item.skill_id,
                    "taxonomy_node_id": item.taxonomy_node_id,
                    "facet": item.facet,
                    "code": item.code,
                    "name_zh": item.name_zh,
                    "name_en": item.name_en,
                    "is_primary": item.is_primary,
                }
                for item in classifications
            ],
            "redirects": [
                {
                    "source_skill_id": item["skill_id"],
                    "target_skill_id": item["redirect_target_skill_id"],
                    "status": item["status"],
                }
                for item in skill_rows
                if item["status"] == "redirected"
            ],
        }

    @staticmethod
    def _catalog_change_summary(
        baseline: dict[str, object] | None,
        current: dict[str, object],
    ) -> dict[str, object]:
        baseline = baseline or {
            "skills": [],
            "aliases": [],
            "classifications": [],
        }
        before_skills = {
            item["skill_id"]: item for item in baseline.get("skills", [])
        }
        current_skills = {
            item["skill_id"]: item for item in current.get("skills", [])
        }
        added_skills = [
            {"skill_id": item["skill_id"], "skill_name": item["skill_name"]}
            for skill_id, item in current_skills.items()
            if skill_id not in before_skills
        ]
        modified_skills = []
        for skill_id, item in current_skills.items():
            before = before_skills.get(skill_id)
            if before is None:
                continue
            changed_fields = sorted(
                key
                for key in item
                if key != "skill_id" and item.get(key) != before.get(key)
            )
            if changed_fields:
                modified_skills.append(
                    {
                        "skill_id": skill_id,
                        "skill_name": item["skill_name"],
                        "changed_fields": changed_fields,
                    }
                )
        before_aliases = {
            (item["skill_id"], normalize_skill_expression(item["alias"]))
            for item in baseline.get("aliases", [])
        }
        added_aliases = [
            item
            for item in current.get("aliases", [])
            if (
                item["skill_id"],
                normalize_skill_expression(item["alias"]),
            )
            not in before_aliases
        ]
        before_classifications = {}
        current_classifications = {}
        for item in baseline.get("classifications", []):
            before_classifications.setdefault(item["skill_id"], set()).add(
                (item["taxonomy_node_id"], item["facet"], item["is_primary"])
            )
        for item in current.get("classifications", []):
            current_classifications.setdefault(item["skill_id"], set()).add(
                (item["taxonomy_node_id"], item["facet"], item["is_primary"])
            )
        classification_adjustments = [
            {
                "skill_id": skill_id,
                "skill_name": current_skills[skill_id]["skill_name"],
            }
            for skill_id in current_skills
            if current_classifications.get(skill_id, set())
            != before_classifications.get(skill_id, set())
        ]
        merged_or_inactive = [
            {
                "skill_id": skill_id,
                "skill_name": item["skill_name"],
                "status": item["status"],
                "redirect_target_skill_id": item["redirect_target_skill_id"],
            }
            for skill_id, item in current_skills.items()
            if item["status"] in {"redirected", "inactive"}
            and (
                skill_id not in before_skills
                or before_skills[skill_id].get("status") != item["status"]
                or before_skills[skill_id].get("redirect_target_skill_id")
                != item["redirect_target_skill_id"]
            )
        ]
        return {
            "added_skills": added_skills,
            "modified_skills": modified_skills,
            "added_aliases": added_aliases,
            "classification_adjustments": classification_adjustments,
            "merged_or_inactive": merged_or_inactive,
            "counts": {
                "added_skills": len(added_skills),
                "modified_skills": len(modified_skills),
                "added_aliases": len(added_aliases),
                "classification_adjustments": len(classification_adjustments),
                "merged_or_inactive": len(merged_or_inactive),
            },
        }

    @staticmethod
    def _catalog_validation_issues(
        uow: SkillUnitOfWork,
        snapshot: dict[str, object],
    ) -> tuple[str, ...]:
        issues = []
        nodes = {
            item.node_id: item for item in uow.skills.list_taxonomy_nodes()
        }
        skills = {item["skill_id"]: item for item in snapshot["skills"]}
        classifications = {}
        for item in snapshot["classifications"]:
            classifications.setdefault(item["skill_id"], []).append(item)
            node = nodes.get(item["taxonomy_node_id"])
            if node is None:
                issues.append(
                    f"Skill {item['skill_id']} references a missing taxonomy node"
                )
            elif node.status != "active":
                issues.append(
                    f"Skill {item['skill_id']} references an inactive taxonomy node"
                )
        for skill_id, skill in skills.items():
            if skill["status"] != "active":
                continue
            rows = classifications.get(skill_id, [])
            concepts = [row for row in rows if row["facet"] == "concept_class"]
            kinds = [row for row in rows if row["facet"] == "technology_kind"]
            domains = [row for row in rows if row["facet"] == "domain"]
            if len(concepts) != 1 or not concepts[0]["is_primary"]:
                issues.append(
                    f"Skill {skill['skill_name']} requires one primary concept_class"
                )
                continue
            if (concepts[0]["code"] == "technology") != (len(kinds) == 1):
                issues.append(
                    f"Skill {skill['skill_name']} has invalid technology_kind"
                )
            elif kinds and not kinds[0]["is_primary"]:
                issues.append(
                    f"Skill {skill['skill_name']} technology_kind must be primary"
                )
            if sum(bool(row["is_primary"]) for row in domains) > 1:
                issues.append(
                    f"Skill {skill['skill_name']} has multiple primary domains"
                )
        aliases = {}
        for item in snapshot["aliases"]:
            normalized = normalize_skill_expression(item["alias"])
            previous = aliases.get(normalized)
            if previous is not None:
                issues.append(f"Duplicate alias: {item['alias']}")
            aliases[normalized] = item["skill_id"]
        for skill in skills.values():
            if skill["status"] != "redirected":
                continue
            target_id = skill["redirect_target_skill_id"]
            target = skills.get(target_id)
            if target is None or target["status"] != "active":
                issues.append(
                    f"Skill {skill['skill_name']} has an invalid redirect target"
                )
        return tuple(issues)
