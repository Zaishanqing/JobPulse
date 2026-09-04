"""Technology-neutral vector and embedding contracts.

These contracts are deliberately disconnected from matching score calculation.
They define the safe boundary that future derived vector indexes may implement.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Literal, get_args
from uuid import UUID, NAMESPACE_URL, uuid5

from pydantic import AliasChoices, Field, computed_field, field_validator, model_validator

from app.domain.privacy import find_pii
from app.domain.profiles import Evidence, ImmutableDTO

EMBEDDING_REQUEST_SCHEMA_VERSION = "embedding-request.v1"
EMBEDDING_RESULT_SCHEMA_VERSION = "embedding-result.v1"
VECTOR_RECORD_SCHEMA_VERSION = "vector-record.v1"
VECTOR_QUERY_SCHEMA_VERSION = "vector-query.v1"
VECTOR_INDEX_REFERENCE_SCHEMA_VERSION = "vector-index-reference.v1"
SEMANTIC_FRAGMENT_MAX_CHARS = 1000

VectorPayloadValue = str | int | float | bool | None
MatchTargetType = Literal["candidate_cv", "standard_position", "enterprise_job"]
CVFragmentType = Literal[
    "cv_summary",
    "work_experience",
    "project",
    "project_responsibility",
    "skill_context",
    "education_context",
    "scenario_evidence",
    "achievement",
]
PositionFragmentType = Literal[
    "position_summary",
    "responsibility",
    "required_skill_context",
    "preferred_skill_context",
    "scenario_requirement",
    "project_expectation",
    "education_requirement",
    "experience_requirement",
]
SemanticTargetType = Literal["responsibility", "project", "scenario"]
CV_FRAGMENT_TYPES = frozenset(get_args(CVFragmentType))
POSITION_FRAGMENT_TYPES = frozenset(get_args(PositionFragmentType))


class VectorContractViolation(ValueError):
    """Stable fail-closed error raised at a vector boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SemanticEvidence(ImmutableDTO):
    schema_version: Literal["semantic-evidence.v1"] = "semantic-evidence.v1"
    evidence_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    target_type: SemanticTargetType
    target_id: str = Field(min_length=1)
    profile_version: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    taxonomy_version: str = Field(min_length=1)
    graph_version: str | None = None
    algorithm_version: str | None = None
    created_at: str = Field(min_length=1)
    evidence_refs: tuple[Evidence, ...] = ()


def _validate_vector(vector: tuple[float, ...], dimension: int) -> tuple[float, ...]:
    if len(vector) != dimension:
        raise ValueError("vector dimension does not match declared dimension")
    if not all(math.isfinite(value) for value in vector):
        raise ValueError("vectors do not accept NaN or infinity")
    return vector


class SemanticFragment(ImmutableDTO):
    schema_version: Literal["semantic-fragment.v1"] = "semantic-fragment.v1"
    tenant_ref: str = Field(min_length=1)
    fragment_id: str = Field(min_length=1)
    source_type: Literal["cv", "position"]
    target_type: MatchTargetType
    source_id: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    source_profile_id: str = Field(
        validation_alias=AliasChoices("source_profile_id", "profile_version"),
        min_length=1,
    )
    fragment_type: CVFragmentType | PositionFragmentType
    normalized_text: str = Field(
        validation_alias=AliasChoices("normalized_text", "text"),
        min_length=1,
        max_length=SEMANTIC_FRAGMENT_MAX_CHARS,
    )
    evidence_ref: Evidence
    language: Literal["zh-Hans", "en", "und"]
    sequence: int = Field(ge=0)
    taxonomy_version: str = Field(min_length=1)
    graph_version: str | None = None
    grant_id: str | None = Field(default=None, min_length=1, max_length=200)
    grant_version: int | None = Field(default=None, ge=1)
    personal_tenant_ref: str | None = Field(default=None, min_length=1, max_length=200)
    enterprise_tenant_ref: str | None = Field(default=None, min_length=1, max_length=200)

    @property
    def profile_version(self) -> str:
        return self.source_profile_id

    @property
    def text(self) -> str:
        return self.normalized_text

    @model_validator(mode="after")
    def reject_pii(self) -> SemanticFragment:
        allowed = CV_FRAGMENT_TYPES if self.source_type == "cv" else POSITION_FRAGMENT_TYPES
        if self.fragment_type not in allowed:
            raise ValueError("fragment type does not belong to the source type")
        projection = (
            self.grant_id,
            self.grant_version,
            self.personal_tenant_ref,
            self.enterprise_tenant_ref,
        )
        if any(value is not None for value in projection) and any(
            value is None for value in projection
        ):
            raise ValueError("enterprise projection lineage must be complete")
        if (
            self.enterprise_tenant_ref is not None
            and self.enterprise_tenant_ref != self.tenant_ref
        ):
            raise ValueError("enterprise projection tenant does not match fragment tenant")
        if find_pii(self.model_dump(mode="python")):
            raise ValueError("semantic fragment contains prohibited PII")
        return self

class EmbeddingRequest(ImmutableDTO):
    schema_version: Literal["embedding-request.v1"] = EMBEDDING_REQUEST_SCHEMA_VERSION
    tenant_ref: str = Field(min_length=1)
    embedding_model: str = Field(min_length=1)
    embedding_revision: str = Field(min_length=1)
    dimension: int = Field(gt=0)
    normalized: Literal[True] = True
    normalization: Literal["l2"] = "l2"
    representation: Literal["dense"] = "dense"
    similarity: Literal["cosine"] = "cosine"
    text_derivation_version: str = Field(default="semantic-fragment.v1", min_length=1)
    fragments: tuple[SemanticFragment, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def enforce_tenant_and_unique_fragments(self) -> EmbeddingRequest:
        if any(item.tenant_ref != self.tenant_ref for item in self.fragments):
            raise ValueError("all embedding fragments must belong to the request tenant")
        fragment_ids = tuple(item.fragment_id for item in self.fragments)
        if len(fragment_ids) != len(set(fragment_ids)):
            raise ValueError("embedding request fragment IDs must be unique")
        return self

    request_id: str = Field(default="embedding-request", min_length=1)


class EmbeddingResult(ImmutableDTO):
    schema_version: Literal["embedding-result.v1"] = EMBEDDING_RESULT_SCHEMA_VERSION
    tenant_ref: str = Field(min_length=1)
    request_id: str = Field(default="embedding-request", min_length=1)
    embedding_model: str = Field(min_length=1)
    embedding_revision: str = Field(min_length=1)
    dimension: int = Field(gt=0)
    normalized: Literal[True] = True
    normalization: Literal["l2"] = "l2"
    representation: Literal["dense"] = "dense"
    similarity: Literal["cosine"] = "cosine"
    text_derivation_version: str = Field(default="semantic-fragment.v1", min_length=1)
    fragment_ids: tuple[str, ...] = Field(min_length=1)
    vectors: tuple[tuple[float, ...], ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_result_vectors(self) -> EmbeddingResult:
        if len(self.fragment_ids) != len(self.vectors):
            raise ValueError("embedding result count does not match fragment count")
        if len(self.fragment_ids) != len(set(self.fragment_ids)):
            raise ValueError("embedding result fragment IDs must be unique")
        for vector in self.vectors:
            _validate_vector(vector, self.dimension)
            norm = math.sqrt(sum(value * value for value in vector))
            if norm == 0 or abs(norm - 1.0) > 1e-3:
                raise ValueError("embedding vectors must be l2-normalized")
        return self


def deterministic_point_id(
    fragment: SemanticFragment,
    *,
    embedding_model: str,
    embedding_revision: str,
    dimension: int,
    normalized: bool = True,
    normalization: str = "l2",
    representation: str = "dense",
    similarity: str = "cosine",
    text_derivation_version: str = "semantic-fragment.v1",
) -> str:
    canonical = (
        f"{fragment.tenant_ref}:{fragment.fragment_id}:{fragment.source_profile_id}:"
        f"{embedding_model}:{embedding_revision}"
    )
    return str(uuid5(NAMESPACE_URL, canonical))


class VectorRecord(ImmutableDTO):
    schema_version: Literal["vector-record.v1"] = VECTOR_RECORD_SCHEMA_VERSION
    point_id: str = Field(min_length=1)
    tenant_ref: str = Field(min_length=1)
    fragment: SemanticFragment
    embedding: tuple[float, ...] = Field(min_length=1)
    embedding_model: str = Field(min_length=1)
    embedding_revision: str = Field(min_length=1)
    normalized: Literal[True] = True
    normalization: Literal["l2"] = "l2"
    representation: Literal["dense"] = "dense"
    similarity: Literal["cosine"] = "cosine"
    text_derivation_version: str = Field(default="semantic-fragment.v1", min_length=1)
    dimension: int = Field(gt=0)
    index_revision: str | None = Field(default=None, min_length=1)
    collection: str | None = Field(default=None, min_length=1)
    payload: dict[str, VectorPayloadValue] = Field(default_factory=dict)
    active: bool = True

    @classmethod
    def build(
        cls,
        *,
        fragment: SemanticFragment,
        embedding: tuple[float, ...],
        embedding_model: str,
        embedding_revision: str,
        payload: dict[str, VectorPayloadValue] | None = None,
        active: bool | None = None,
        index_revision: str | None = None,
        collection: str | None = None,
        text_derivation_version: str = "semantic-fragment.v1",
    ) -> VectorRecord:
        dimension = len(embedding)
        return cls(
            point_id=deterministic_point_id(
                fragment,
                embedding_model=embedding_model,
                embedding_revision=embedding_revision,
                dimension=dimension,
                text_derivation_version=text_derivation_version,
            ),
            tenant_ref=fragment.tenant_ref,
            fragment=fragment,
            embedding=embedding,
            embedding_model=embedding_model,
            embedding_revision=embedding_revision,
            text_derivation_version=text_derivation_version,
            dimension=dimension,
            index_revision=index_revision,
            collection=collection,
            payload=payload or {},
            active=True if active is None else active,
        )

    @model_validator(mode="after")
    def validate_record(self) -> VectorRecord:
        if self.tenant_ref != self.fragment.tenant_ref:
            raise ValueError("vector record tenant must match fragment tenant")
        _validate_vector(self.embedding, self.dimension)
        expected_point_id = deterministic_point_id(
            self.fragment,
            embedding_model=self.embedding_model,
            embedding_revision=self.embedding_revision,
            dimension=self.dimension,
            normalized=self.normalized,
            normalization=self.normalization,
            representation=self.representation,
            similarity=self.similarity,
            text_derivation_version=self.text_derivation_version,
        )
        if self.point_id != expected_point_id:
            raise ValueError("point ID does not match deterministic record identity")
        if find_pii(self.payload):
            raise ValueError("vector payload contains prohibited PII")
        norm = math.sqrt(sum(value * value for value in self.embedding))
        if norm == 0 or abs(norm - 1.0) > 1e-3:
            raise ValueError("vector record embedding must be l2-normalized")
        return self


class VectorPointSnapshot(ImmutableDTO):
    """Minimal Qdrant inventory used by reconciliation without exposing vectors."""

    point_id: str = Field(min_length=1)
    tenant_ref: str = Field(min_length=1)
    entity_type: Literal["cv", "position"]
    entity_id: str = Field(min_length=1)
    fragment_id: str = Field(min_length=1)
    profile_version: str = Field(min_length=1)
    embedding_revision: str = Field(min_length=1)
    embedding_revision: str = Field(min_length=1)
    active: bool


class VectorFilter(ImmutableDTO):
    active: Literal[True] = True
    profile_version: str | None = Field(default=None, min_length=1)
    fragment_types: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    target_types: tuple[MatchTargetType, ...] = ()


class VectorQuery(ImmutableDTO):
    schema_version: Literal["vector-query.v1"] = VECTOR_QUERY_SCHEMA_VERSION
    tenant_ref: str = Field(min_length=1)
    embedding: tuple[float, ...] = Field(min_length=1)
    embedding_model: str = Field(min_length=1)
    embedding_revision: str = Field(min_length=1)
    normalized: Literal[True] = True
    normalization: Literal["l2"] = "l2"
    representation: Literal["dense"] = "dense"
    similarity: Literal["cosine"] = "cosine"
    text_derivation_version: str = Field(default="semantic-fragment.v1", min_length=1)
    index_revision: str | None = Field(default=None, min_length=1)
    collection: str | None = Field(default=None, min_length=1)
    dimension: int = Field(gt=0)
    filter: VectorFilter = VectorFilter()
    top_k: int = Field(default=10, ge=1, le=100)

    @model_validator(mode="after")
    def validate_query_vector(self) -> VectorQuery:
        _validate_vector(self.embedding, self.dimension)
        norm = math.sqrt(sum(value * value for value in self.embedding))
        if norm == 0 or abs(norm - 1.0) > 1e-3:
            raise ValueError("vector query embedding must be l2-normalized")
        return self


class VectorSearchHit(ImmutableDTO):
    point_id: str = Field(min_length=1)
    tenant_ref: str = Field(min_length=1)
    fragment: SemanticFragment
    score: float = Field(ge=-1, le=1)
    payload: dict[str, VectorPayloadValue] = Field(default_factory=dict)

    @field_validator("score")
    @classmethod
    def reject_non_finite_score(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("vector search score must be finite")
        return value

    @model_validator(mode="after")
    def validate_hit_boundary(self) -> VectorSearchHit:
        if self.tenant_ref != self.fragment.tenant_ref:
            raise ValueError("vector search hit tenant must match fragment tenant")
        if find_pii(self.payload):
            raise ValueError("vector search hit payload contains prohibited PII")
        return self


class VectorIndexReference(ImmutableDTO):
    schema_version: Literal["vector-index-reference.v1"] = VECTOR_INDEX_REFERENCE_SCHEMA_VERSION
    index_name: str = Field(min_length=1)
    tenant_ref: str = Field(min_length=1)
    point_id: str = Field(min_length=1)
    fragment_id: str = Field(min_length=1)
    active: bool
    indexed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


__all__ = [
    "EmbeddingRequest",
    "EmbeddingResult",
    "MatchTargetType",
    "CVFragmentType",
    "PositionFragmentType",
    "CV_FRAGMENT_TYPES",
    "POSITION_FRAGMENT_TYPES",
    "SemanticEvidence",
    "SemanticFragment",
    "SEMANTIC_FRAGMENT_MAX_CHARS",
    "VectorContractViolation",
    "VectorFilter",
    "VectorIndexReference",
    "VectorQuery",
    "VectorRecord",
    "VectorSearchHit",
    "deterministic_point_id",
]
