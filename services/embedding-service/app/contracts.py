from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class EmbeddingRequest(StrictModel):
    inputs: tuple[str, ...] = Field(min_length=1, max_length=64)
    normalize: bool = True

    @field_validator("normalize")
    @classmethod
    def require_normalize(cls, value: bool) -> bool:
        if not value:
            raise ValueError("normalize must be true")
        return value

    @field_validator("inputs")
    @classmethod
    def validate_inputs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            if not value.strip():
                raise ValueError("embedding inputs cannot be empty")
            if len(value) > 4096:
                raise ValueError("embedding input exceeds 4096 characters")
        return values


class Usage(StrictModel):
    input_count: int = Field(ge=1)
    character_count: int = Field(ge=1)


class EmbeddingResponse(StrictModel):
    vectors: tuple[tuple[float, ...], ...]
    model_id: str = Field(min_length=1)
    model_revision: str = Field(min_length=1)
    dimension: int = Field(gt=0)
    normalized: bool
    usage: Usage
    latency_ms: float = Field(ge=0)

    @field_validator("normalized")
    @classmethod
    def require_normalized(cls, value: bool) -> bool:
        if not value:
            raise ValueError("embedding responses must be normalized")
        return value

    @field_validator("vectors")
    @classmethod
    def reject_non_finite_vectors(
        cls, vectors: tuple[tuple[float, ...], ...]
    ) -> tuple[tuple[float, ...], ...]:
        if any(not all(math.isfinite(item) for item in vector) for vector in vectors):
            raise ValueError("embedding vectors must contain finite values")
        return vectors


class ErrorResponse(StrictModel):
    code: str
    message: str
