from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from app.contexts.data_validation.ports import (
    CrossSourceDuplicatePort,
    SkillCatalogReference,
    SkillCatalogResolution,
    SkillCatalogResolutionPort,
    SkillCatalogResolutionStatus,
)
from app.domain.jd_skill_catalog import CatalogClassification, CatalogResolution


@dataclass
class FakeSkillCatalogResolutionPort(SkillCatalogResolutionPort):
    responses: Mapping[str, SkillCatalogResolution] = field(default_factory=dict)
    default_status: SkillCatalogResolutionStatus = "resolved"
    taxonomy_version: str = "catalog-v1"
    classification_sets: Mapping[
        str, tuple[str, tuple[CatalogClassification, ...]]
    ] = field(default_factory=dict)

    def resolve(self, reference: SkillCatalogReference) -> SkillCatalogResolution:
        return self.responses.get(
            reference.source_name,
            CatalogResolution(
                self.default_status,
                reference.claimed_skill_id,
                reference.claimed_canonical_name,
                None,
                "fake",
            ),
        )

    def classification_set(self, catalog_code: str):
        return self.classification_sets.get(catalog_code)


@dataclass
class FakeCrossSourceDuplicatePort(CrossSourceDuplicatePort):
    sources_by_hash: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def find_sources(self, canonical_hash: str) -> tuple[str, ...]:
        return self.sources_by_hash.get(canonical_hash, ())
