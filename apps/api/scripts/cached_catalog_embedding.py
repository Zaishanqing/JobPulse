"""Caching wrapper for catalog embedding calls.

The dual-recall Normalization ranker embeds the query plus the same full
representation list on every call.  This wrapper keeps the skill-side vectors
from the first call and only re-embeds the query on subsequent calls, which
makes real unresolved queue evaluation tractable.
"""

from __future__ import annotations

import httpx

from app.contexts.catalog._ports.normalization_suggestions import (
    CatalogEmbeddingError,
)


class CatalogEmbeddingClient:
    """embedding-service.v1 client matching the running service contract."""

    def __init__(
        self,
        base_url: str,
        *,
        model: str,
        revision: str,
        dimension: int,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._endpoint = f"{base_url.rstrip('/')}/v1/embeddings"
        self._model = model
        self._revision = revision
        self._dimension = dimension
        self._timeout = timeout_seconds

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        vectors: list[tuple[float, ...]] = []
        for start in range(0, len(texts), 64):
            batch = texts[start : start + 64]
            vectors.extend(self._embed_batch(batch))
        return tuple(vectors)

    def _embed_batch(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        try:
            response = httpx.post(
                self._endpoint,
                json={"inputs": list(texts), "normalize": True},
                timeout=self._timeout,
            )
        except httpx.RequestError as exc:
            raise CatalogEmbeddingError(str(exc)) from exc
        if response.status_code >= 400:
            raise CatalogEmbeddingError(
                f"embedding service returned HTTP {response.status_code}"
            )
        payload = response.json()
        lineage_ok = (
            payload.get("model_id") == self._model
            and payload.get("model_revision") == self._revision
            and payload.get("dimension") == self._dimension
            and payload.get("normalized") is True
        )
        if not lineage_ok:
            raise CatalogEmbeddingError("embedding lineage mismatch")
        vectors = payload.get("vectors")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise CatalogEmbeddingError("embedding response count mismatch")
        return tuple(tuple(vector) for vector in vectors)


class CachedCatalogEmbedding:
    def __init__(self, adapter) -> None:
        self._adapter = adapter
        self._cached_vectors: tuple[tuple[float, ...], ...] | None = None

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        if self._cached_vectors is None:
            vectors = self._adapter.embed(texts)
            self._cached_vectors = tuple(vectors[1:])
            return vectors
        query_vector = self._adapter.embed((texts[0],))[0]
        return (query_vector, *self._cached_vectors)
