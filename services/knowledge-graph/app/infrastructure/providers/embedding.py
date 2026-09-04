"""BGE embedding-service v1 client for EMERGE v2.1 responsibility alignment.

Mirrors the repository's formal embedding contract used by matching-service
(``app/infrastructure/http_embedding_adapter.py``): ``POST {base}/v1/embeddings``
with ``{"inputs": [...], "normalize": true}`` and a strict lineage-validated
response.  No model is trained here; this is the existing BGE deployment.

The client fails closed (raises on any contract violation).  Callers decide
whether to fall back to the deterministic lexical encoder; when they do, the
result must be marked ``degraded`` and never mixed into formal metrics.
"""

from __future__ import annotations

import math
import os

import httpx


class EmbeddingContractViolation(RuntimeError):
    """Raised when the embedding service is unavailable or violates v1."""


class BgeEmbeddingClient:
    """Strict client for the repository's formal embedding-service v1."""

    def __init__(
        self,
        *,
        base_url: str,
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

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = httpx.post(
                self._endpoint,
                json={"inputs": texts, "normalize": True},
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise EmbeddingContractViolation(
                "EMBEDDING_TIMEOUT: embedding service request timed out"
            ) from exc
        except httpx.RequestError as exc:
            raise EmbeddingContractViolation(
                "EMBEDDING_UNAVAILABLE: embedding service is unavailable"
            ) from exc
        if response.status_code >= 400:
            code = (
                "EMBEDDING_UNAVAILABLE"
                if response.status_code >= 500
                else "EMBEDDING_REQUEST_REJECTED"
            )
            raise EmbeddingContractViolation(
                f"{code}: embedding service returned HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise EmbeddingContractViolation(
                "EMBEDDING_RESPONSE_INVALID: response is not JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise EmbeddingContractViolation(
                "EMBEDDING_RESPONSE_INVALID: response must be an object"
            )
        vectors = payload.get("vectors")
        if (
            not isinstance(vectors, list)
            or len(vectors) != len(texts)
            or any(not isinstance(vector, list) for vector in vectors)
        ):
            raise EmbeddingContractViolation(
                "EMBEDDING_RESPONSE_INVALID: vectors do not match input count"
            )
        lineage_checks = (
            (payload.get("model_id"), self._model, "EMBEDDING_MODEL_ID_MISMATCH"),
            (
                payload.get("model_revision"),
                self._revision,
                "EMBEDDING_REVISION_MISMATCH",
            ),
            (
                payload.get("dimension"),
                self._dimension,
                "EMBEDDING_DIMENSION_MISMATCH",
            ),
            (payload.get("normalized"), True, "EMBEDDING_NORMALIZATION_MISMATCH"),
            (payload.get("representation", "dense"), "dense", "EMBEDDING_REPRESENTATION_MISMATCH"),
            (payload.get("similarity", "cosine"), "cosine", "EMBEDDING_SIMILARITY_MISMATCH"),
        )
        for actual, expected, code in lineage_checks:
            if actual != expected:
                raise EmbeddingContractViolation(f"{code}: expected {expected!r}, got {actual!r}")
        result: list[list[float]] = []
        for vector in vectors:
            if len(vector) != self._dimension or any(
                not isinstance(value, (int, float)) or not math.isfinite(float(value))
                for value in vector
            ):
                raise EmbeddingContractViolation(
                    "EMBEDDING_RESPONSE_INVALID: vector dimension/values invalid"
                )
            result.append([float(value) for value in vector])
        return result

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]


def build_bge_client_from_env(
    env: dict[str, str] | None = None,
) -> BgeEmbeddingClient | None:
    """Build the formal client from matching-service env config; None if absent."""
    values = env if env is not None else os.environ
    base_url = values.get("MATCHING_EMBEDDING_ENDPOINT", "").strip()
    model = values.get("MATCHING_VECTOR_EMBEDDING_MODEL", "").strip()
    revision = values.get("MATCHING_VECTOR_EMBEDDING_REVISION", "").strip()
    if not base_url or not model or not revision:
        return None
    dimension = int(values.get("MATCHING_QDRANT_DIMENSION", "1024"))
    timeout = float(values.get("MATCHING_EMBEDDING_TIMEOUT_SECONDS", "10"))
    return BgeEmbeddingClient(
        base_url=base_url,
        model=model,
        revision=revision,
        dimension=dimension,
        timeout_seconds=timeout,
    )


__all__ = [
    "BgeEmbeddingClient",
    "EmbeddingContractViolation",
    "build_bge_client_from_env",
]
