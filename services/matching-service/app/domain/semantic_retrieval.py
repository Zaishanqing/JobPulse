"""Auditable contracts for dense semantic retrieval and explanations."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from app.domain.privacy import find_pii
from app.domain.profiles import Evidence, ImmutableDTO

SemanticMode = Literal["disabled", "shadow"]
SemanticStatus = Literal["disabled", "available", "unavailable"]


class SemanticRetrievalEvidence(ImmutableDTO):
    query_fragment_id: str = Field(min_length=1)
    candidate_fragment_id: str = Field(min_length=1)
    query_fragment_type: str = Field(min_length=1)
    candidate_fragment_type: str = Field(min_length=1)
    candidate_source_id: str = Field(min_length=1)
    similarity: float = Field(ge=-1, le=1)
    rank: int = Field(ge=1)
    dense_rank: int | None = Field(default=None, ge=1)
    sparse_rank: int | None = Field(default=None, ge=1)
    rrf_score: float = Field(default=0.0, ge=0)
    retrieval_score: float | None = None
    rerank_score: float | None = None
    final_rank: int | None = Field(default=None, ge=1)
    evidence_ref: Evidence
    position_evidence_ref: Evidence
    profile_version: str = Field(min_length=1)
    embedding_model: str = Field(default="embedding.unknown", min_length=1)
    embedding_revision: str = Field(min_length=1)
    embedding_dimension: int = Field(default=1024, gt=0)
    embedding_normalized: bool = True
    embedding_normalization: Literal["l2"] = "l2"
    vector_representation: Literal["dense"] = "dense"
    vector_similarity: Literal["cosine"] = "cosine"
    text_derivation_version: str = Field(
        default="semantic-fragment.v1", min_length=1
    )
    index_revision: str | None = None
    collection: str | None = None
    reranker_model_revision: str | None = None
    retrieval_trace_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_lineage_and_ranks(self) -> SemanticRetrievalEvidence:
        if self.retrieval_score is not None and not -1 <= self.retrieval_score <= 1:
            raise ValueError("retrieval score must be within -1..1")
        if self.rerank_score is not None and not -1 <= self.rerank_score <= 1:
            raise ValueError("rerank score must be within -1..1")
        return self


class SemanticCandidate(ImmutableDTO):
    candidate_source_id: str = Field(min_length=1)
    score: float = Field(ge=-1, le=1)
    evidence: tuple[SemanticRetrievalEvidence, ...] = Field(min_length=1)
    retrieval_score: float | None = Field(default=None, ge=-1, le=1)
    rerank_score: float | None = Field(default=None, ge=-1, le=1)
    final_rank: int | None = Field(default=None, ge=1)
    reranker_model_revision: str | None = None
    degraded: bool = False
    degradation_reason: str | None = None


class SemanticMatchExplanation(ImmutableDTO):
    dimension: Literal[
        "skill_semantic_match",
        "responsibility_semantic_match",
        "project_semantic_match",
        "scenario_semantic_match",
    ]
    match_kind: Literal["semantic_related"] = "semantic_related"
    score: float = Field(ge=-1, le=1)
    position_text: str = Field(min_length=1)
    resume_evidence: str = Field(min_length=1)
    evidence_ref: str = Field(min_length=1)
    embedding_revision: str = Field(min_length=1)

    @model_validator(mode="after")
    def reject_pii(self) -> SemanticMatchExplanation:
        if find_pii(self.model_dump(mode="python")):
            raise ValueError("semantic explanation contains prohibited PII")
        return self


class SemanticRetrievalResult(ImmutableDTO):
    status: SemanticStatus
    error_code: str | None = None
    candidates: tuple[SemanticCandidate, ...] = ()
    hit_count: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0)
    embedding_revision: str | None = None
    embedding_model: str | None = None
    embedding_dimension: int | None = Field(default=None, gt=0)
    embedding_normalized: bool | None = None
    embedding_normalization: Literal["l2"] | None = None
    vector_representation: Literal["dense"] | None = None
    vector_similarity: Literal["cosine"] | None = None
    text_derivation_version: str | None = None
    index_revision: str | None = None
    collection: str | None = None
    retrieval_trace_id: str | None = None
    algorithm_version: str | None = None
    reranker_model_revision: str | None = None
    reranker_status: Literal["disabled", "applied", "degraded"] = "disabled"
    degradation_reason: str | None = None


__all__ = [
    "SemanticCandidate",
    "SemanticMatchExplanation",
    "SemanticMode",
    "SemanticRetrievalEvidence",
    "SemanticRetrievalResult",
    "SemanticStatus",
]
