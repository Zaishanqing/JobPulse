from __future__ import annotations

import math

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.contexts.catalog import CatalogEmbeddingError


class _Usage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_count: int = Field(ge=1)
    character_count: int = Field(ge=1)


class _EmbeddingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vectors: tuple[tuple[float, ...], ...]
    model_id: str = Field(min_length=1)
    model_revision: str = Field(min_length=1)
    dimension: int = Field(gt=0)
    normalized: bool
    usage: _Usage
    latency_ms: float = Field(ge=0)

    @field_validator("vectors")
    @classmethod
    def finite_vectors(
        cls, vectors: tuple[tuple[float, ...], ...]
    ) -> tuple[tuple[float, ...], ...]:
        if any(not all(math.isfinite(value) for value in vector) for vector in vectors):
            raise ValueError("embedding vectors must contain finite values")
        return vectors


class HttpCatalogEmbedding:
    """Strict adapter for embedding-service.v1, isolated from Evidence RAG errors."""

    def __init__(
        self,
        base_url: str,
        *,
        model: str,
        revision: str,
        dimension: int,
        timeout_seconds: float = 5.0,
    ) -> None:
        if not base_url.strip() or not model.strip() or not revision.strip():
            raise ValueError("Catalog embedding URL and model lineage are required")
        if dimension <= 0 or timeout_seconds <= 0:
            raise ValueError("Catalog embedding dimension and timeout must be positive")
        self._endpoint = f"{base_url.rstrip('/')}/v1/embeddings"
        self._model = model
        self._revision = revision
        self._dimension = dimension
        self._timeout = timeout_seconds

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        try:
            response = httpx.post(
                self._endpoint,
                json={"inputs": list(texts), "normalize": True},
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise CatalogEmbeddingError("Catalog embedding request timed out") from exc
        except httpx.RequestError as exc:
            raise CatalogEmbeddingError("Catalog embedding service is unavailable") from exc
        if response.status_code >= 400:
            raise CatalogEmbeddingError(
                f"Catalog embedding service returned HTTP {response.status_code}"
            )
        try:
            result = _EmbeddingResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise CatalogEmbeddingError(
                "Catalog embedding response does not satisfy embedding-service.v1"
            ) from exc
        if (
            result.model_id != self._model
            or result.model_revision != self._revision
            or result.dimension != self._dimension
            or not result.normalized
            or len(result.vectors) != len(texts)
            or any(len(vector) != self._dimension for vector in result.vectors)
        ):
            raise CatalogEmbeddingError(
                "Catalog embedding response lineage or vector shape is invalid"
            )
        return result.vectors


__all__ = ["HttpCatalogEmbedding"]
