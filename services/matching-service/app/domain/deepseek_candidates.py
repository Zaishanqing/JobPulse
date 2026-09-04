"""Contracts for DeepSeek LLM skill candidate recall and validation."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from app.domain.privacy import find_pii
from app.domain.profiles import Evidence, ImmutableDTO

DeepSeekRelationType = Literal["exact", "equivalent", "related", "transferable", "unknown"]
DeepSeekSemanticMode = Literal["disabled", "shadow", "enabled"]


class RawDeepSeekSkillCandidate(ImmutableDTO):
    requirement_id: str = Field(min_length=1)
    required_skill_id: str = Field(min_length=1)
    required_skill_name: str = Field(min_length=1)
    candidate_skill_id: str = Field(min_length=1)
    candidate_skill_name: str = Field(min_length=1)
    proposed_relation_type: DeepSeekRelationType
    rationale: str = Field(min_length=1)
    position_quote: str | None = None
    candidate_quote: str | None = None

    @model_validator(mode="after")
    def reject_pii(self) -> RawDeepSeekSkillCandidate:
        if find_pii(self.model_dump(mode="python")):
            raise ValueError("DeepSeek candidate contains prohibited PII")
        return self


class LLMSemanticCandidateBatch(ImmutableDTO):
    model: str = Field(min_length=1)
    algorithm_version: str = Field(min_length=1)
    candidates: tuple[RawDeepSeekSkillCandidate, ...] = ()


class SkillSemanticCandidate(ImmutableDTO):
    requirement_id: str = Field(min_length=1)
    required_skill_id: str = Field(min_length=1)
    required_skill_name: str = Field(min_length=1)
    candidate_skill_id: str = Field(min_length=1)
    candidate_skill_name: str = Field(min_length=1)
    proposed_relation_type: DeepSeekRelationType
    relation_type: DeepSeekRelationType
    status: Literal["valid", "unknown"]
    reason_code: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    position_evidence: tuple[Evidence, ...] = ()
    candidate_evidence: tuple[Evidence, ...] = ()
    relation_evidence: tuple[Evidence, ...] = ()
    relation_source: str | None = None
    relation_graph_version: str | None = None
    model: str = Field(min_length=1)
    algorithm_version: str = Field(min_length=1)


class DeepSeekSemanticResult(ImmutableDTO):
    status: Literal["disabled", "available", "unavailable"]
    error_code: str | None = None
    candidates: tuple[SkillSemanticCandidate, ...] = ()
    model: str | None = None
    algorithm_version: str | None = None


__all__ = [
    "DeepSeekRelationType",
    "DeepSeekSemanticMode",
    "DeepSeekSemanticResult",
    "LLMSemanticCandidateBatch",
    "RawDeepSeekSkillCandidate",
    "SkillSemanticCandidate",
]
