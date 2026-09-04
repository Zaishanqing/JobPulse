from __future__ import annotations

import math

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.contexts.evidence_rag.contracts import EvidenceRagError


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
    normalization: str = Field(default="l2", min_length=1)
    representation: str = Field(default="dense", min_length=1)
    similarity: str = Field(default="cosine", min_length=1)
    usage: _Usage
    latency_ms: float = Field(ge=0)


class HttpEvidenceRagEmbedding:
    def __init__(
        self,
        base_url: str,
        *,
        model: str,
        revision: str,
        dimension: int,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not base_url.strip() or not model.strip() or not revision.strip():
            raise ValueError("embedding URL and model lineage are required")
        if dimension <= 0 or timeout_seconds <= 0:
            raise ValueError("embedding dimension and timeout must be positive")
        self._endpoint = f"{base_url.rstrip('/')}/v1/embeddings"
        self._model = model
        self._revision = revision
        self._dimension = dimension
        self._timeout = timeout_seconds

    def embed(self, text: str) -> list[float]:
        return list(self._embed((text,))[0])

    def embed_many(
        self, texts: tuple[str, ...]
    ) -> tuple[tuple[float, ...], ...]:
        if not texts:
            raise EvidenceRagError(
                "EMBEDDING_REQUEST_INVALID",
                "embedding service requires at least one input",
            )
        return self._embed(texts)

    def _embed(
        self, texts: tuple[str, ...]
    ) -> tuple[tuple[float, ...], ...]:
        try:
            response = httpx.post(
                self._endpoint,
                json={"inputs": list(texts), "normalize": True},
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise EvidenceRagError(
                "EMBEDDING_TIMEOUT", "embedding service request timed out"
            ) from exc
        except httpx.RequestError as exc:
            raise EvidenceRagError(
                "EMBEDDING_UNAVAILABLE", "embedding service is unavailable"
            ) from exc
        if response.status_code >= 400:
            code = (
                "EMBEDDING_UNAVAILABLE"
                if response.status_code >= 500
                else "EMBEDDING_REQUEST_REJECTED"
            )
            raise EvidenceRagError(
                code, f"embedding service returned HTTP {response.status_code}"
            )
        try:
            result = _EmbeddingResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise EvidenceRagError(
                "EMBEDDING_RESPONSE_INVALID",
                "embedding service response does not satisfy embedding-service.v1",
            ) from exc
        lineage = (
            (result.model_id, self._model, "EMBEDDING_MODEL_ID_MISMATCH"),
            (result.model_revision, self._revision, "EMBEDDING_REVISION_MISMATCH"),
            (result.dimension, self._dimension, "EMBEDDING_DIMENSION_MISMATCH"),
        )
        for actual, expected, code in lineage:
            if actual != expected:
                raise EvidenceRagError(
                    code, "embedding service lineage does not match configuration"
                )
        if (
            not result.normalized
            or result.normalization != "l2"
            or result.representation != "dense"
            or result.similarity != "cosine"
        ):
            raise EvidenceRagError(
                "EMBEDDING_LINEAGE_MISMATCH",
                "embedding service must expose l2 normalized dense cosine vectors",
            )
        if len(result.vectors) != len(texts):
            raise EvidenceRagError(
                "EMBEDDING_RESPONSE_INVALID",
                "embedding service must return exactly one vector per input",
            )
        vectors = tuple(tuple(vector) for vector in result.vectors)
        if any(
            len(vector) != self._dimension
            or not all(math.isfinite(value) for value in vector)
            for vector in vectors
        ):
            raise EvidenceRagError(
                "EMBEDDING_VECTOR_INVALID",
                "embedding service returned invalid vector values",
            )
        return vectors

    def model_id(self) -> str:
        return self._model

    def model_revision(self) -> str:
        return self._revision

    def dimension(self) -> int:
        return self._dimension


__all__ = ["HttpEvidenceRagEmbedding"]
