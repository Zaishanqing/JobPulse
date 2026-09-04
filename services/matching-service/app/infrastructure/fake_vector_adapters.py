"""Deterministic, process-local adapters for B1 contract tests only."""

from __future__ import annotations

import math
from datetime import datetime, timezone

from app.domain.privacy import find_pii
from app.domain.vector_contracts import (
    EmbeddingRequest,
    EmbeddingResult,
    VectorContractViolation,
    VectorIndexReference,
    VectorPointSnapshot,
    VectorQuery,
    VectorRecord,
    VectorSearchHit,
)


def _deterministic_vector(seed: str, dimension: int) -> tuple[float, ...]:
    values: list[float] = []
    counter = 0
    while len(values) < dimension:
        raw = f"{seed}:{counter}"
        values.extend(((ord(char) % 255) / 127.5) - 1.0 for char in raw)
        counter += 1
    vector = tuple(values[:dimension])
    norm = math.sqrt(sum(value * value for value in vector))
    return tuple(value / norm for value in vector) if norm else vector


class FakeEmbeddingAdapter:
    """Pure deterministic embedding adapter; it never contacts a model service."""

    def __init__(self, *, model: str, revision: str, dimension: int) -> None:
        if not model or not revision or dimension <= 0:
            raise ValueError("model, revision and a positive dimension are required")
        self.model = model
        self.revision = revision
        self.dimension = dimension

    def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        if (
            request.embedding_model != self.model
            or request.embedding_revision != self.revision
            or request.dimension != self.dimension
        ):
            raise VectorContractViolation(
                "EMBEDDING_LINEAGE_MISMATCH",
                "embedding request does not match adapter model lineage",
            )
        vectors = tuple(
            _deterministic_vector(
                f"{self.model}:{self.revision}:{fragment.fragment_id}",
                self.dimension,
            )
            for fragment in request.fragments
        )
        return EmbeddingResult(
            tenant_ref=request.tenant_ref,
            request_id=request.request_id,
            embedding_model=self.model,
            embedding_revision=self.revision,
            dimension=self.dimension,
            text_derivation_version=request.text_derivation_version,
            fragment_ids=tuple(item.fragment_id for item in request.fragments),
            vectors=vectors,
        )


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


class FakeVectorStoreAdapter:
    """Tenant-partitioned in-memory derived index for deterministic tests."""

    index_name = "fake-derived-vector-index"

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], VectorRecord] = {}

    def upsert(self, records: tuple[VectorRecord, ...]) -> tuple[VectorIndexReference, ...]:
        references: list[VectorIndexReference] = []
        for record in records:
            if find_pii(record.payload):
                raise VectorContractViolation(
                    "VECTOR_PAYLOAD_CONTAINS_PII",
                    "vector payload contains prohibited PII",
                )
            key = (record.tenant_ref, record.point_id)
            existing = self._records.get(key)
            if existing is not None and existing.model_dump(exclude={"active"}) != (
                record.model_dump(exclude={"active"})
            ):
                raise VectorContractViolation(
                    "VECTOR_POINT_ID_COLLISION",
                    "deterministic point ID refers to different record content",
                )
            self._records[key] = record
            references.append(
                VectorIndexReference(
                    index_name=self.index_name,
                    tenant_ref=record.tenant_ref,
                    point_id=record.point_id,
                    fragment_id=record.fragment.fragment_id,
                    active=record.active,
                    indexed_at=datetime.now(timezone.utc),
                )
            )
        return tuple(references)

    def search(self, query: VectorQuery) -> tuple[VectorSearchHit, ...]:
        hits: list[VectorSearchHit] = []
        allowed_types = frozenset(query.filter.fragment_types)
        allowed_sources = frozenset(query.filter.source_ids)
        allowed_targets = frozenset(query.filter.target_types)
        for (tenant_ref, _point_id), record in self._records.items():
            if tenant_ref != query.tenant_ref or not record.active:
                continue
            if (
                record.embedding_model != query.embedding_model
                or record.embedding_revision != query.embedding_revision
                or record.dimension != query.dimension
            ):
                continue
            if (
                query.filter.profile_version is not None
                and record.fragment.profile_version != query.filter.profile_version
            ):
                continue
            if allowed_types and record.fragment.fragment_type not in allowed_types:
                continue
            if allowed_sources and record.fragment.source_id not in allowed_sources:
                continue
            if allowed_targets and record.fragment.target_type not in allowed_targets:
                continue
            hits.append(
                VectorSearchHit(
                    point_id=record.point_id,
                    tenant_ref=record.tenant_ref,
                    fragment=record.fragment,
                    score=max(-1.0, min(1.0, _cosine(query.embedding, record.embedding))),
                    payload=record.payload,
                )
            )
        hits.sort(key=lambda item: (-item.score, item.point_id))
        return tuple(hits[: query.top_k])

    def delete(self, *, tenant_ref: str, point_ids: tuple[str, ...]) -> None:
        for point_id in point_ids:
            self._records.pop((tenant_ref, point_id), None)

    def deactivate(self, *, tenant_ref: str, point_ids: tuple[str, ...]) -> None:
        for point_id in point_ids:
            key = (tenant_ref, point_id)
            record = self._records.get(key)
            if record is not None:
                self._records[key] = record.model_copy(update={"active": False})

    def activate(self, *, tenant_ref: str, point_ids: tuple[str, ...]) -> None:
        for point_id in point_ids:
            key = (tenant_ref, point_id)
            record = self._records.get(key)
            if record is not None:
                self._records[key] = record.model_copy(update={"active": True})

    def health(self) -> None:
        return None

    def list_points(self, *, tenant_ref=None, embedding_revision=None):
        points = (
            VectorPointSnapshot(
                point_id=record.point_id,
                tenant_ref=record.tenant_ref,
                entity_type=record.fragment.source_type,
                entity_id=record.fragment.source_id,
                fragment_id=record.fragment.fragment_id,
                profile_version=record.fragment.source_profile_id,
                embedding_revision=record.embedding_revision,
                active=record.active,
            )
            for record in self._records.values()
            if (tenant_ref is None or record.tenant_ref == tenant_ref)
            and (embedding_revision is None or record.embedding_revision == embedding_revision)
        )
        return tuple(sorted(points, key=lambda item: (item.tenant_ref, item.point_id)))
