from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, JsonValue, RootModel

from app.contracts.position_taxonomy_v3 import (
    CareerLevel,
    IndustryContextCode,
    LeadershipScope,
    TechnologyFocusCode,
)
from app.domain.json_types import freeze_json_object
from app.domain.input_limits import MAX_BATCH_SIZE, MAX_JD_TEXT_CHARS
from app.domain.jd_policies import JDParseEditCommand


class JDTextCreate(BaseModel):
    source_type: str = "enterprise_upload"
    source_name: str | None = None
    enterprise_id: str | None = None
    title: str = Field(min_length=1, max_length=255)
    raw_text: str = Field(min_length=1, max_length=MAX_JD_TEXT_CHARS)
    cleaned_text: str | None = None
    publish_date: date | None = None
    url: str | None = None


JDTextBatch = Annotated[list[JDTextCreate], Field(max_length=MAX_BATCH_SIZE)]


class JDBatchCreateRequest(RootModel[JDTextBatch]):
    pass


class JDParseBatchRequest(BaseModel):
    jd_ids: list[str] = Field(min_length=1, max_length=MAX_BATCH_SIZE)
    extraction_mode: Literal["llm", "rule"]


class JDRawTextUpdate(BaseModel):
    raw_text: str = Field(min_length=1, max_length=MAX_JD_TEXT_CHARS)


class JDResponse(BaseModel):
    jd_id: str
    source_type: str
    source_name: str | None = None
    enterprise_id: str | None = None
    title: str
    raw_text: str
    publish_date: date | None = None
    url: str | None = None
    parse_status: str
    copy_risk_score: float | None = None
    inflation_score: float | None = None
    is_downweighted: bool


class JDParseRequest(BaseModel):
    extraction_mode: Literal["llm", "rule"]
    model: str = "default"
    use_skill_dictionary: bool = True
    auto_normalize_skill: bool = True


class JDParseResultResponse(BaseModel):
    jd_id: str
    position_title: str | None = None
    responsibilities: list[str]
    required_skills: list[dict[str, Any]]
    bonus_skills: list[dict[str, Any]]
    education: str | None = None
    experience: str | None = None
    industry: str | None = None
    tools: list[str]
    business_scenarios: list[str]
    parse_confidence: float
    need_review: bool


class JDSkillCatalogMappingRequest(BaseModel):
    source_name: str = Field(min_length=1, max_length=128)
    target_skill_id: str = Field(min_length=1, max_length=80)
    requirement_id: str | None = Field(default=None, max_length=128)


class JDSkillCatalogExclusionRequest(BaseModel):
    source_name: str = Field(min_length=1, max_length=128)
    requirement_id: str | None = Field(default=None, max_length=128)
    reason: str = Field(min_length=1, max_length=500)


class JDPositionCatalogMappingRequest(BaseModel):
    target_position_id: str = Field(min_length=1, max_length=80)
    career_level: CareerLevel | None = None
    leadership_scope: LeadershipScope | None = None
    technology_focus_codes: list[TechnologyFocusCode] | None = None
    industry_context_codes: list[IndustryContextCode] | None = None


class JDParseResultUpdate(BaseModel):
    position_title: str | None = None
    responsibilities: list[str] | None = None
    required_skills: list[dict[str, Any]] | None = None
    bonus_skills: list[dict[str, Any]] | None = None
    education: str | None = None
    experience: str | None = None
    industry: str | None = None
    tools: list[str] | None = None
    business_scenarios: list[str] | None = None
    parse_confidence: float | None = Field(default=None, ge=0, le=1)
    need_review: bool | None = None
    extraction_result: dict[str, JsonValue] | None = None
    normalized_result: dict[str, JsonValue] | None = None

    def to_command(self) -> JDParseEditCommand:
        values = self.model_dump(exclude_unset=True, mode="json")
        return JDParseEditCommand(
            changed_fields=frozenset(self.model_fields_set),
            parse_confidence=values.get("parse_confidence"),
            need_review=values.get("need_review"),
            extraction_result=(
                freeze_json_object(values["extraction_result"], field="extraction_result")
                if values.get("extraction_result") is not None
                else None
            ),
            normalized_result=(
                freeze_json_object(values["normalized_result"], field="normalized_result")
                if values.get("normalized_result") is not None
                else None
            ),
        )


class DuplicateCheckResponse(BaseModel):
    jd_id: str
    copy_risk_score: float
    similar_jds: list[dict[str, Any]]
    recommended_action: str
    reason: str


class InflationCheckResponse(BaseModel):
    jd_id: str
    inflation_score: float
    abnormal_skills: list[dict[str, str]]
    recommended_action: str
