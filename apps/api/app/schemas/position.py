from typing import Any

from pydantic import BaseModel, Field


class StandardPositionCreate(BaseModel):
    position_code: str | None = Field(default=None, min_length=1, max_length=100)
    position_name: str = Field(min_length=1, max_length=255)
    taxonomy_family_code: str | None = Field(default=None, min_length=1, max_length=80)
    taxonomy_family_name: str | None = Field(default=None, min_length=1, max_length=120)
    skill_domain_codes: list[str] = []
    core_responsibilities: list[str] = []
    required_skills: list[dict[str, Any]] = []
    bonus_skills: list[dict[str, Any]] = []
    industry_scenarios: list[str] = []
    status: str = "existing"


class StandardPositionUpdate(BaseModel):
    position_code: str | None = Field(default=None, min_length=1, max_length=100)
    position_name: str | None = Field(default=None, min_length=1, max_length=255)
    taxonomy_family_code: str | None = Field(default=None, min_length=1, max_length=80)
    taxonomy_family_name: str | None = Field(default=None, min_length=1, max_length=120)
    skill_domain_codes: list[str] | None = None
    core_responsibilities: list[str] | None = None
    required_skills: list[dict[str, Any]] | None = None
    bonus_skills: list[dict[str, Any]] | None = None
    industry_scenarios: list[str] | None = None
    status: str | None = None


class PositionSkillRelationUpdate(BaseModel):
    required_skills: list[dict[str, Any]] | None = None
    bonus_skills: list[dict[str, Any]] | None = None


class StandardPositionResponse(BaseModel):
    position_id: str
    position_name: str
    required_skills: list[dict[str, Any]]
    bonus_skills: list[dict[str, Any]]
    status: str
