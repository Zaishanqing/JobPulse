from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class EmergingPositionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position_name: str | None = None
    core_responsibilities: list[str] | None = None
    required_skills: list[dict[str, Any]] | None = None
    bonus_skills: list[dict[str, Any]] | None = None
    industry_scenarios: list[str] | None = None
    field_evidence: dict[str, Any] | None = None


class EmergingPositionReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conclusion: Literal["approved", "rejected"]
    reason: str
    position_name: str | None = None
    core_responsibilities: list[str] | None = None
    required_skills: list[dict[str, Any]] | None = None
    field_evidence: dict[str, Any] | None = None


class EmergingPositionResponse(BaseModel):
    emerging_id: str
    cluster_id: str
    position_name: str
    core_responsibilities: list[str]
    required_skills: list[dict[str, Any]]
    bonus_skills: list[dict[str, Any]]
    industry_scenarios: list[str]
    germination_score: float | None = None
    score_dimensions: dict[str, Any]
    evidence_jd_ids: list[str]
    status: str
    field_evidence: dict[str, Any] = {}
    review_history: list[dict[str, Any]] = []
    published_snapshot: dict[str, Any] = {}
    standard_position: dict[str, Any] | None = None


class EmergingPositionGenerationResponse(EmergingPositionResponse):
    definition_version_id: str
    generation_mode: str
    evidence_ids: list[str]


class EmergingPositionGenerationEnvelope(BaseModel):
    code: Literal[0]
    message: str
    data: EmergingPositionGenerationResponse
    trace_id: str
