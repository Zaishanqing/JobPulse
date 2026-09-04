"""Immutable graph relation contract owned by the matching boundary."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from app.domain.privacy import find_pii
from app.domain.profiles import Evidence, ImmutableDTO

SkillRelationType = Literal[
    "equivalent",
    "parent_child",
    "prerequisite",
    "related",
    "transferable",
]


class SkillRelation(ImmutableDTO):
    relation_id: str = Field(min_length=1)
    source_skill_id: str = Field(min_length=1)
    target_skill_id: str = Field(min_length=1)
    relation_type: SkillRelationType
    source_system: str = Field(min_length=1)
    graph_version: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    evidence_refs: tuple[Evidence, ...]

    @model_validator(mode="after")
    def reject_self_relation(self) -> SkillRelation:
        if self.source_skill_id == self.target_skill_id:
            raise ValueError("skill relation endpoints must differ")
        violations = find_pii(self.model_dump(mode="python"))
        if violations:
            raise ValueError(
                "PII is forbidden in skill relations: "
                + ", ".join(item.path for item in violations)
            )
        return self
