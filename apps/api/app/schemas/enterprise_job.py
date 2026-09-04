from typing import Literal

from pydantic import BaseModel, Field
from jobgraph_contracts.extraction_v2 import RequirementGraph


EnterpriseJobStatus = Literal["draft", "published", "paused", "cancelled"]
EnterpriseJobSalaryUnit = Literal["year", "month", "day"]


class EnterpriseJobCreateRequest(BaseModel):
    enterprise_id: str
    title: str = Field(min_length=1, max_length=255)
    standard_position_id: str | None = None
    jd_text: str | None = None
    requirement_graph: RequirementGraph | None = None
    headcount: int = Field(default=1, ge=0)
    location: str | None = None
    employment_type: str | None = None
    salary_min: int | None = Field(default=None, ge=0)
    salary_max: int | None = Field(default=None, ge=0)
    salary_unit: EnterpriseJobSalaryUnit = "month"
    status: EnterpriseJobStatus = "draft"


class EnterpriseJobUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    standard_position_id: str | None = None
    jd_text: str | None = None
    requirement_graph: RequirementGraph | None = None
    headcount: int | None = Field(default=None, ge=0)
    location: str | None = None
    employment_type: str | None = None
    salary_min: int | None = Field(default=None, ge=0)
    salary_max: int | None = Field(default=None, ge=0)
    salary_unit: EnterpriseJobSalaryUnit | None = None
    status: EnterpriseJobStatus | None = None


class HeadcountUpdateRequest(BaseModel):
    headcount: int = Field(ge=0)
    reason: str | None = None


class EnterpriseJobSkillWeightItem(BaseModel):
    skill_id: str
    weight: float = Field(ge=0)
    is_required: bool = False
    is_bonus: bool = False


class EnterpriseJobSkillWeightsRequest(BaseModel):
    weights: list[EnterpriseJobSkillWeightItem]
