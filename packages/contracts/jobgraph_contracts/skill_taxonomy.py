from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from jobgraph_contracts.base import StrictContract


class SkillClassificationV1(StrictContract):
    facet: Literal["concept_class", "technology_kind", "domain"]
    code: str = Field(min_length=1, max_length=80)
    name_zh: str | None = Field(default=None, max_length=120)
    name_en: str | None = Field(default=None, max_length=120)
    is_primary: bool


class SkillClassificationSetV1(StrictContract):
    skill_id: str = Field(min_length=1, max_length=80)
    canonical_name: str = Field(min_length=1, max_length=150)
    classifications: list[SkillClassificationV1]

    @model_validator(mode="after")
    def validate_cardinality(self) -> "SkillClassificationSetV1":
        concepts = [x for x in self.classifications if x.facet == "concept_class"]
        kinds = [x for x in self.classifications if x.facet == "technology_kind"]
        domains = [x for x in self.classifications if x.facet == "domain"]
        if len(concepts) != 1 or not concepts[0].is_primary:
            raise ValueError("exactly one primary concept_class is required")
        if (concepts[0].code == "technology") != (len(kinds) == 1):
            raise ValueError("technology_kind conflicts with concept_class")
        if kinds and not kinds[0].is_primary:
            raise ValueError("technology_kind must be primary")
        if sum(item.is_primary for item in domains) > 1:
            raise ValueError("at most one primary domain is allowed")
        keys = {(item.facet, item.code) for item in self.classifications}
        if len(keys) != len(self.classifications):
            raise ValueError("duplicate skill classification")
        return self


class SkillTaxonomyProjectionV1(StrictContract):
    schema_version: Literal["skill-taxonomy-projection.v1"] = (
        "skill-taxonomy-projection.v1"
    )
    taxonomy_version: str = Field(min_length=1, max_length=64)
    skills: list[SkillClassificationSetV1]
