"""Stable KG-to-consumer skill relation snapshot contract."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from jobgraph_contracts.base import StrictContract
from jobgraph_contracts.skill_taxonomy import (
    SkillClassificationSetV1,
    SkillClassificationV1,
)


class RelationEvidenceRefV1(StrictContract):
    support_id: int = Field(ge=1)
    evidence_id: int = Field(ge=1)
    document_id: str = Field(min_length=1)
    requirement_id: str = Field(min_length=1)


class SkillRelationV1(StrictContract):
    skill_id: str = Field(min_length=1)
    canonical_name: str = Field(min_length=1)
    category_code: str = Field(min_length=1)
    subcategory_code: str | None = None
    primary_modality: Literal["required", "preferred", "bonus", "unknown"]
    weight: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    importance_level: str = Field(min_length=1)
    evidence_refs: list[RelationEvidenceRefV1]


class SkillRelationSnapshotV1(StrictContract):
    contract_version: Literal["skill-relation-snapshot.v1"]
    position_id: str = Field(min_length=1)
    graph_version_id: int = Field(ge=1)
    release_id: str | None = None
    watermark_version: str = Field(min_length=1)
    graph_version: str = Field(min_length=1)
    authority_state: Literal["authoritative", "observed"]
    generated_at: datetime
    relations: list[SkillRelationV1]


class SkillRelationV2(StrictContract):
    skill_id: str = Field(min_length=1)
    canonical_name: str = Field(min_length=1)
    classifications: list[SkillClassificationV1]
    taxonomy_version: str = Field(min_length=1)
    primary_modality: Literal["required", "preferred", "bonus", "unknown"]
    weight: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    importance_level: str = Field(min_length=1)
    evidence_refs: list[RelationEvidenceRefV1]

    @model_validator(mode="after")
    def validate_classifications(self) -> "SkillRelationV2":
        SkillClassificationSetV1(
            skill_id=self.skill_id,
            canonical_name=self.canonical_name,
            classifications=self.classifications,
        )
        return self


class SkillRelationSnapshotV2(StrictContract):
    contract_version: Literal["skill-relation-snapshot.v2"]
    position_id: str = Field(min_length=1)
    graph_version_id: int = Field(ge=1)
    release_id: str | None = None
    watermark_version: str = Field(min_length=1)
    graph_version: str = Field(min_length=1)
    authority_state: Literal["authoritative", "observed"]
    generated_at: datetime
    relations: list[SkillRelationV2]
