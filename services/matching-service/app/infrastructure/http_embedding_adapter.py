"""HTTP implementation of the B1 EmbeddingPort for embedding-service v1."""

from __future__ import annotations

import math

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domain.vector_contracts import (
    EmbeddingRequest,
    EmbeddingResult,
    VectorContractViolation,
)


class _Usage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    input_count: int = Field(ge=1)
    character_count: int = Field(ge=1)


class _EmbeddingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    vectors: tuple[tuple[float, ...], ...]
    model_id: str = Field(min_length=1)
    model_revision: str = Field(min_length=1)
    dimension: int = Field(gt=0)
    normalized: bool
    normalization: str = "l2"
    representation: str = "dense"
    similarity: str = "cosine"
    usage: _Usage
    latency_ms: float = Field(ge=0)


class _ModelDescription(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    model_id: str = Field(min_length=1)
    model_revision: str = Field(min_length=1)
    dimension: int = Field(gt=0)
    representation: str = Field(min_length=1)
    similarity: str = Field(min_length=1)
    normalized: bool = True
    normalization: str = "l2"
    # Runtime placement metadata is published by embedding-service but is not
    # part of Matching's lineage checks. Keep it typed so the shared contract
    # remains strict without rejecting a valid CPU/GPU deployment description.
    device: str | None = None
    use_fp16: bool | None = None


class HttpEmbeddingAdapter:
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
        self._health_endpoint = f"{base_url.rstrip('/')}/ready"
        self._model_endpoint = f"{base_url.rstrip('/')}/v1/models/current"
        self._model = model
        self._revision = revision
        self._dimension = dimension
        self._timeout = timeout_seconds

    def check_health(self) -> None:
        try:
            response = httpx.get(self._health_endpoint, timeout=self._timeout)
        except httpx.HTTPError as exc:
            raise VectorContractViolation(
                "EMBEDDING_UNAVAILABLE", "embedding service is unavailable"
            ) from exc
        if response.status_code >= 400:
            raise VectorContractViolation(
                "EMBEDDING_UNAVAILABLE",
                f"embedding service returned HTTP {response.status_code}",
            )

    def check_model_contract(self) -> None:
        """Validate the immutable model lineage before accepting shadow traffic."""
        try:
            response = httpx.get(self._model_endpoint, timeout=self._timeout)
        except httpx.TimeoutException as exc:
            raise VectorContractViolation(
                "EMBEDDING_TIMEOUT", "embedding model contract request timed out"
            ) from exc
        except httpx.RequestError as exc:
            raise VectorContractViolation(
                "EMBEDDING_UNAVAILABLE", "embedding model contract is unavailable"
            ) from exc
        if response.status_code >= 400:
            raise VectorContractViolation(
                "EMBEDDING_UNAVAILABLE",
                f"embedding model contract returned HTTP {response.status_code}",
            )
        try:
            description = _ModelDescription.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise VectorContractViolation(
                "EMBEDDING_RESPONSE_INVALID",
                "embedding model contract response is invalid",
            ) from exc
        checks = (
            (
                description.model_id,
                self._model,
                "EMBEDDING_MODEL_ID_MISMATCH",
                "embedding model id does not match configuration",
            ),
            (
                description.model_revision,
                self._revision,
                "EMBEDDING_REVISION_MISMATCH",
                "embedding model revision does not match configuration",
            ),
            (
                description.dimension,
                self._dimension,
                "EMBEDDING_DIMENSION_MISMATCH",
                "embedding model dimension does not match configuration",
            ),
        )
        for actual, expected, code, message in checks:
            if actual != expected:
                raise VectorContractViolation(code, message)
        if description.representation != "dense":
            raise VectorContractViolation(
                "EMBEDDING_REPRESENTATION_MISMATCH",
                "embedding service must expose dense vectors",
            )
        if description.similarity != "cosine":
            raise VectorContractViolation(
                "EMBEDDING_SIMILARITY_MISMATCH",
                "embedding service must expose cosine similarity",
            )
        if not description.normalized:
            raise VectorContractViolation(
                "EMBEDDING_NORMALIZATION_MISMATCH",
                "embedding service must return normalized vectors",
            )
        if description.normalization != "l2":
            raise VectorContractViolation(
                "EMBEDDING_NORMALIZATION_MISMATCH",
                "embedding service must use l2 normalization",
            )

    check_startup_contract = check_model_contract

    def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        if (
            request.embedding_model != self._model
            or request.embedding_revision != self._revision
            or request.dimension != self._dimension
        ):
            raise VectorContractViolation(
                "EMBEDDING_LINEAGE_MISMATCH",
                "embedding request does not match configured service lineage",
            )
        try:
            response = httpx.post(
                self._endpoint,
                json={
                    "inputs": [item.normalized_text for item in request.fragments],
                    "normalize": True,
                },
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise VectorContractViolation(
                "EMBEDDING_TIMEOUT", "embedding service request timed out"
            ) from exc
        except httpx.RequestError as exc:
            raise VectorContractViolation(
                "EMBEDDING_UNAVAILABLE", "embedding service is unavailable"
            ) from exc
        if response.status_code >= 400:
            code = "EMBEDDING_UNAVAILABLE" if response.status_code >= 500 else (
                "EMBEDDING_REQUEST_REJECTED"
            )
            raise VectorContractViolation(
                code, f"embedding service returned HTTP {response.status_code}"
            )
        try:
            result = _EmbeddingResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise VectorContractViolation(
                "EMBEDDING_RESPONSE_INVALID",
                "embedding service response does not satisfy embedding-service.v1",
            ) from exc
        if (
            result.model_id != self._model
            or result.model_revision != self._revision
            or result.dimension != self._dimension
            or not result.normalized
            or result.normalization != "l2"
            or result.representation != "dense"
            or result.similarity != "cosine"
        ):
            raise VectorContractViolation(
                "EMBEDDING_LINEAGE_MISMATCH",
                "embedding service returned incompatible model lineage",
            )
        if len(result.vectors) != len(request.fragments):
            raise VectorContractViolation(
                "EMBEDDING_RESPONSE_INVALID",
                "embedding result count does not match request",
            )
        if any(
            len(vector) != self._dimension
            or not all(math.isfinite(value) for value in vector)
            for vector in result.vectors
        ):
            raise VectorContractViolation(
                "EMBEDDING_VECTOR_INVALID",
                "embedding service returned invalid vector values",
            )
        return EmbeddingResult(
            tenant_ref=request.tenant_ref,
            request_id=request.request_id,
            embedding_model=result.model_id,
            embedding_revision=result.model_revision,
            dimension=result.dimension,
            normalized=True,
            normalization="l2",
            representation="dense",
            similarity="cosine",
            text_derivation_version=request.text_derivation_version,
            fragment_ids=tuple(item.fragment_id for item in request.fragments),
            vectors=result.vectors,
        )


__all__ = ["HttpEmbeddingAdapter"]
