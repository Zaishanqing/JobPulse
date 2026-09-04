from typing import Any

from pydantic import BaseModel, Field


class EvidenceRetrieveRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)


class EvidenceGenerateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)


class EvidenceGenerationUpdate(BaseModel):
    text: str = Field(min_length=1)


class EvidenceValidateRequest(BaseModel):
    text: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)


class EvidenceValidateResponse(BaseModel):
    valid: bool
    coverage_score: float
    unsupported_claims: list[str]
    evidence_ids: list[str]
    details: dict[str, Any] = Field(default_factory=dict)
