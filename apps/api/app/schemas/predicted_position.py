from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class PredictionTaskRequest(BaseModel):
    source_ids: list[str] = Field(default_factory=list)
    window_start: datetime | None = None
    window_end: datetime | None = None
    data_sources: list[str] | None = None


class PredictedPositionUpdate(BaseModel):
    position_name: str | None = Field(default=None, min_length=1, max_length=255)
    prediction_basis: list[dict[str, Any]] | None = None
    related_source_ids: list[str] | None = None
    potential_responsibilities: list[str] | None = None
    potential_skills: list[str] | None = None
    industry_scenarios: list[str] | None = None
    confidence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    status: str | None = None


class PredictionDefinitionUpdate(BaseModel):
    position_name: str | None = Field(default=None, min_length=1, max_length=255)
    core_responsibilities: list[str] | None = None
    required_skills: list[str | dict[str, Any]] | None = None
    bonus_skills: list[str | dict[str, Any]] | None = None
    industry_scenarios: list[str] | None = None
    formation_basis: list[dict[str, Any]] | None = None
    evidence_by_conclusion: dict[str, Any] | None = None


class PredictionReviewSubmit(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


class PredictionPublishRequest(BaseModel):
    definition_id: str | None = None


class PredictionRelationRequest(BaseModel):
    relation_type: str
    target_id: str | None = None
    reason: str | None = Field(default=None, max_length=1000)
