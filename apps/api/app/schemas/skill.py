from typing import Literal

from pydantic import BaseModel, Field

from app.domain.input_limits import MAX_BATCH_SIZE


class SkillCreate(BaseModel):
    skill_name: str = Field(min_length=1, max_length=128)
    category: str | None = None
    description: str | None = None
    parent_skill_id: str | None = None
    aliases: list[str] = []


class SkillUpdate(BaseModel):
    skill_name: str | None = Field(default=None, min_length=1, max_length=128)
    category: str | None = None
    description: str | None = None
    parent_skill_id: str | None = None


class SkillResponse(BaseModel):
    skill_id: str
    skill_name: str
    catalog_code: str | None = None
    category: str | None = None
    description: str | None = None
    parent_skill_id: str | None = None
    status: Literal["active", "redirected", "inactive"] = "active"
    redirect_target_skill_id: str | None = None


class SkillAliasCreate(BaseModel):
    alias: str = Field(min_length=1, max_length=128)


class SkillTaxonomyNodeCreate(BaseModel):
    facet: Literal["concept_class", "technology_kind", "domain"]
    code: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    name_zh: str = Field(min_length=1, max_length=120)
    name_en: str | None = Field(default=None, max_length=120)
    parent_id: str | None = None
    status: Literal["active", "inactive"] = "active"


class SkillTaxonomyNodeUpdate(BaseModel):
    name_zh: str | None = Field(default=None, min_length=1, max_length=120)
    name_en: str | None = Field(default=None, max_length=120)
    parent_id: str | None = None
    status: Literal["active", "inactive"] | None = None


class SkillClassificationCreate(BaseModel):
    taxonomy_node_id: str
    is_primary: bool = False


class SkillNormalizeRequest(BaseModel):
    raw_skill: str = Field(min_length=1, max_length=128)
    context: str | None = None
    source_type: Literal["jd", "cv", "manual", "unknown"] = "unknown"
    evidence: str | None = None


class SkillNormalizationSuggestionsRequest(BaseModel):
    raw_skill: str = Field(min_length=1, max_length=128)
    context: str | None = Field(default=None, max_length=4096)
    top_k: int = Field(default=5, ge=1, le=20)


class SkillNormalizeResponse(BaseModel):
    raw_skill: str
    candidates: list[dict]
    need_review: bool
    candidate_id: str | None = None


class SkillNormalizeBatchRequest(BaseModel):
    items: list[SkillNormalizeRequest] = Field(max_length=MAX_BATCH_SIZE)


class CandidateConfirmRequest(BaseModel):
    skill_id: str
    decision_reason: str = Field(min_length=1, max_length=1000)


class CandidateReasonRequest(BaseModel):
    decision_reason: str = Field(min_length=1, max_length=1000)


class CandidateMapExistingRequest(BaseModel):
    skill_id: str
    add_alias: bool = False
    decision_reason: str = Field(min_length=1, max_length=1000)


class CandidateCreateNewRequest(BaseModel):
    skill_name: str = Field(min_length=1, max_length=128)
    category: str | None = None
    description: str | None = None
    concept_class_id: str
    technology_kind_id: str | None = None
    domain_id: str
    add_alias: bool = False
    decision_reason: str = Field(min_length=1, max_length=1000)


class SkillMergeRequest(BaseModel):
    source_skill_id: str
    target_skill_id: str
