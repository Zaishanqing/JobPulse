from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domain.json_types import FrozenJsonObject


@dataclass(frozen=True)
class SkillRecord:
    skill_id: str
    skill_name: str
    catalog_code: str | None
    category: str | None
    description: str | None
    parent_skill_id: str | None
    status: str
    redirect_target_skill_id: str | None
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class SkillDraft:
    skill_name: str
    category: str | None
    description: str | None
    parent_skill_id: str | None


@dataclass(frozen=True)
class SkillChanges:
    changed_fields: frozenset[str]
    skill_name: str | None = None
    category: str | None = None
    description: str | None = None
    parent_skill_id: str | None = None


@dataclass(frozen=True)
class SkillTaxonomyNodeRecord:
    node_id: str
    facet: str
    code: str
    name_zh: str
    name_en: str | None
    parent_id: str | None
    status: str
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class SkillTaxonomyNodeDraft:
    facet: str
    code: str
    name_zh: str
    name_en: str | None
    parent_id: str | None
    status: str = "active"


@dataclass(frozen=True)
class SkillTaxonomyNodeChanges:
    changed_fields: frozenset[str]
    name_zh: str | None = None
    name_en: str | None = None
    parent_id: str | None = None
    status: str | None = None


@dataclass(frozen=True)
class SkillClassificationRecord:
    classification_id: str
    skill_id: str
    taxonomy_node_id: str
    facet: str
    code: str
    name_zh: str
    name_en: str | None
    is_primary: bool
    created_at: datetime | None



@dataclass(frozen=True)
class SkillAliasRecord:
    alias_id: str
    skill_id: str
    alias: str


@dataclass(frozen=True)
class NormalizationCandidateRecord:
    candidate_id: str
    raw_skill: str
    normalized_skill: str
    candidate_skill_id: str | None
    candidate_skill_name: str | None
    confidence: float
    context: str | None
    occurrence_count: int
    source_type: str
    evidence_samples: tuple[dict[str, str], ...]
    status: str
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    reviewer_id: str | None
    reviewed_at: datetime | None
    decision_reason: str | None
    normalization_catalog_version: str | None
    normalized_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class NormalizedSkillCandidate:
    skill_id: str
    skill_name: str
    category: str | None
    confidence: float
    redirected_from_skill_id: str | None = None
    redirected_from_skill_name: str | None = None


@dataclass(frozen=True)
class NormalizationResult:
    raw_skill: str
    candidates: tuple[NormalizedSkillCandidate, ...]
    need_review: bool
    candidate_id: str | None


@dataclass(frozen=True)
class MergeResult:
    source_skill_id: str
    target_skill_id: str
    target_skill_name: str
    source_status: str


@dataclass(frozen=True)
class MergeSkillSummary:
    skill: SkillRecord
    alias_count: int
    classifications: tuple[SkillClassificationRecord, ...]
    related_candidate_count: int


@dataclass(frozen=True)
class MergePreview:
    source: MergeSkillSummary
    target: MergeSkillSummary
    impact_by_source: FrozenJsonObject
    classification_conflicts: tuple[str, ...]


@dataclass(frozen=True)
class CatalogDraftPreview:
    based_on_catalog_version: str | None
    change_summary: FrozenJsonObject
    validation_issues: tuple[str, ...]


@dataclass(frozen=True)
class SkillCatalogVersionRecord:
    version_id: str
    version_number: int
    catalog_version: str
    snapshot: FrozenJsonObject
    change_summary: FrozenJsonObject
    published_by: str
    published_at: datetime | None


@dataclass(frozen=True)
class RenormalizationSummary:
    catalog_version: str
    resolved_candidate_count: int
    unresolved_candidate_count: int
    excluded_non_skill_count: int
    affected_jd_count: int
    affected_cv_count: int


@dataclass(frozen=True)
class DownstreamSkillProjection:
    catalog_version: str
    resolved_skill_ids: tuple[str, ...]
    unresolved_candidates: tuple[NormalizationCandidateRecord, ...]


class SkillRepository(Protocol):
    def add(self, draft: SkillDraft) -> SkillRecord: ...
    def get(self, skill_id: str) -> SkillRecord | None: ...
    def list_skills(self) -> list[SkillRecord]: ...
    def update(self, skill_id: str, changes: SkillChanges) -> SkillRecord: ...
    def delete(self, skill_id: str) -> None: ...
    def add_alias(self, skill_id: str, alias: str) -> SkillAliasRecord: ...
    def list_aliases(self, skill_id: str | None = None) -> list[SkillAliasRecord]: ...
    def delete_alias(self, skill_id: str, alias_id: str) -> bool: ...
    def add_candidate(
        self,
        raw_skill: str,
        context: str | None,
        source_type: str,
        evidence: str | None,
    ) -> NormalizationCandidateRecord: ...
    def get_candidate(self, candidate_id: str) -> NormalizationCandidateRecord | None: ...
    def get_candidate_by_expression(
        self, normalized_skill: str
    ) -> NormalizationCandidateRecord | None: ...
    def list_candidates(
        self,
        status: str | None = None,
        keyword: str | None = None,
        source_type: str | None = None,
    ) -> list[NormalizationCandidateRecord]: ...
    def set_candidate_status(
        self,
        candidate_id: str,
        status: str,
        skill_id: str | None,
        reviewer_id: str,
        reason: str | None,
    ) -> NormalizationCandidateRecord: ...
    def record_candidate_normalization(
        self,
        candidate_id: str,
        status: str,
        skill_id: str | None,
        catalog_version: str,
    ) -> NormalizationCandidateRecord: ...
    def merge(self, source_skill_id: str, target_skill_id: str) -> None: ...
    def latest_catalog_version(self) -> SkillCatalogVersionRecord | None: ...
    def get_catalog_version(
        self, catalog_version: str
    ) -> SkillCatalogVersionRecord | None: ...
    def add_catalog_version(
        self,
        version_number: int,
        catalog_version: str,
        snapshot: FrozenJsonObject,
        change_summary: FrozenJsonObject,
        published_by: str,
    ) -> SkillCatalogVersionRecord: ...
    def add_taxonomy_node(
        self, draft: SkillTaxonomyNodeDraft
    ) -> SkillTaxonomyNodeRecord: ...
    def get_taxonomy_node(
        self, node_id: str
    ) -> SkillTaxonomyNodeRecord | None: ...
    def list_taxonomy_nodes(
        self, facet: str | None = None
    ) -> list[SkillTaxonomyNodeRecord]: ...
    def update_taxonomy_node(
        self,
        node_id: str,
        changes: SkillTaxonomyNodeChanges,
    ) -> SkillTaxonomyNodeRecord: ...
    def add_classification(
        self,
        skill_id: str,
        node: SkillTaxonomyNodeRecord,
        is_primary: bool,
    ) -> SkillClassificationRecord: ...
    def get_classification(
        self, classification_id: str
    ) -> SkillClassificationRecord | None: ...
    def list_classifications(
        self, skill_id: str
    ) -> list[SkillClassificationRecord]: ...
    def list_domain_classifications(self) -> list[tuple[str, str]]: ...
    def delete_classification(
        self, skill_id: str, classification_id: str
    ) -> bool: ...


class SkillUnitOfWork(Protocol):
    skills: SkillRepository
    def __enter__(self) -> "SkillUnitOfWork": ...
    def __exit__(self, exc_type, exc, traceback) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
