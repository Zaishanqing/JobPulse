"""Stage E sparse retrieval and reranker ports; no local production fallback."""

from __future__ import annotations

from typing import Protocol

from pydantic import Field, model_validator

from app.domain.privacy import find_pii
from app.domain.profiles import ImmutableDTO
from app.domain.vector_contracts import SemanticFragment, VectorFilter


class SparseQuery(ImmutableDTO):
    tenant_ref: str = Field(min_length=1)
    fragment: SemanticFragment
    filter: VectorFilter
    index_revision: str = Field(min_length=1)
    top_k: int = Field(ge=1, le=100)


class SparseHit(ImmutableDTO):
    tenant_ref: str = Field(min_length=1)
    fragment: SemanticFragment
    score: float = Field(ge=0, le=1)
    active: bool
    superseded: bool
    profile_version: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    index_revision: str = Field(min_length=1)


class SparseRetrievalPort(Protocol):
    def search(self, query: SparseQuery) -> tuple[SparseHit, ...]: ...


class RerankItem(ImmutableDTO):
    retrieval_item_id: str = Field(min_length=1)
    candidate_fragment_id: str = Field(min_length=1)
    query_text: str = Field(min_length=1)
    candidate_text: str = Field(min_length=1)
    retrieval_score: float = Field(ge=-1, le=1)

    @model_validator(mode="after")
    def reject_pii(self) -> RerankItem:
        if find_pii({"query_text": self.query_text, "candidate_text": self.candidate_text}):
            raise ValueError("reranker input contains prohibited PII")
        return self


class RerankRequest(ImmutableDTO):
    tenant_ref: str = Field(min_length=1)
    model_revision: str = Field(min_length=1)
    items: tuple[RerankItem, ...] = Field(min_length=1, max_length=50)
    top_n: int = Field(ge=1, le=20)


class RerankScore(ImmutableDTO):
    retrieval_item_id: str = Field(min_length=1)
    score: float = Field(ge=-1, le=1)


class RerankerPort(Protocol):
    def rerank(self, request: RerankRequest) -> tuple[RerankScore, ...]: ...


__all__ = [
    "RerankItem",
    "RerankRequest",
    "RerankScore",
    "RerankerPort",
    "SparseHit",
    "SparseQuery",
    "SparseRetrievalPort",
]
