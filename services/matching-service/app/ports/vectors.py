"""Technology-neutral ports for derived embedding and vector indexes."""

from __future__ import annotations

from typing import Protocol

from app.domain.vector_contracts import (
    EmbeddingRequest,
    EmbeddingResult,
    VectorIndexReference,
    VectorPointSnapshot,
    VectorQuery,
    VectorRecord,
    VectorSearchHit,
)


class EmbeddingPort(Protocol):
    def embed(self, request: EmbeddingRequest) -> EmbeddingResult: ...


class VectorStorePort(Protocol):
    def upsert(self, records: tuple[VectorRecord, ...]) -> tuple[VectorIndexReference, ...]: ...

    def search(self, query: VectorQuery) -> tuple[VectorSearchHit, ...]: ...

    def deactivate(self, *, tenant_ref: str, point_ids: tuple[str, ...]) -> None: ...

    def activate(self, *, tenant_ref: str, point_ids: tuple[str, ...]) -> None: ...

    def delete(self, *, tenant_ref: str, point_ids: tuple[str, ...]) -> None: ...

    def health(self) -> None: ...

    def list_points(
        self,
        *,
        tenant_ref: str | None = None,
        embedding_revision: str | None = None,
    ) -> tuple[VectorPointSnapshot, ...]: ...
