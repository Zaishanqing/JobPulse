"""Stage D dense retrieval against the authoritative derived vector index."""

from __future__ import annotations

import time
from dataclasses import dataclass
from uuid import uuid4

from app.domain.feature_flags import FeatureFlagController
from app.domain.profiles import CVMatchProfile, PositionMatchProfile
from app.domain.semantic_fragments import fragment_position_profile
from app.domain.semantic_retrieval import (
    SemanticCandidate,
    SemanticMatchExplanation,
    SemanticMode,
    SemanticRetrievalEvidence,
    SemanticRetrievalResult,
)
from app.domain.vector_contracts import (
    EmbeddingRequest,
    VectorContractViolation,
    VectorFilter,
    VectorQuery,
)
from app.ports.observability import MetricsCollector, NullMetricsCollector
from app.ports.retrieval import (
    RerankerPort,
    RerankItem,
    RerankRequest,
    SparseQuery,
    SparseRetrievalPort,
)
from app.ports.vectors import EmbeddingPort, VectorStorePort

_CANDIDATE_TYPES: dict[str, tuple[str, ...]] = {
    "position_summary": ("cv_summary", "work_experience", "project"),
    "responsibility": ("work_experience", "project_responsibility"),
    "required_skill_context": ("skill_context",),
    "preferred_skill_context": ("skill_context",),
    "scenario_requirement": ("scenario_evidence",),
    "project_expectation": ("project", "project_responsibility"),
}


@dataclass(frozen=True)
class SemanticRetrievalConfig:
    mode: SemanticMode = "disabled"
    embedding_model: str = "embedding.disabled"
    embedding_revision: str = "embedding.disabled"
    embedding_dimension: int = 0
    index_revision: str = "index.disabled"
    collection: str = "collection.disabled"
    vector_text_derivation_version: str = "semantic-fragment.v1"
    semantic_algorithm_version: str = "semantic-shadow.v1"
    threshold_config_version: str = "semantic-shadow-disabled.v1"
    semantic_weight: float = 0.0
    top_k_per_fragment: int = 20
    top_n_candidates: int = 50
    dense_enabled: bool = True
    sparse_enabled: bool = False
    dense_rrf_weight: float = 0.7
    sparse_rrf_weight: float = 0.3
    rrf_k: int = 30
    fusion_top_k: int = 10
    hybrid_threshold: float = 0.7
    reranker_enabled: bool = False
    reranker_model_revision: str = "reranker.disabled"
    reranker_top_k: int = 50
    reranker_top_n: int = 10
    max_latency_ms: float = 1000.0
    disabled_tenant_refs: frozenset[str] = frozenset()
    disabled_target_types: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.mode not in {"disabled", "shadow"}:
            raise ValueError("semantic mode must be disabled or shadow")
        if self.mode != "disabled" and (
            (self.dense_enabled and (
                not self.embedding_model
                or not self.embedding_revision
                or self.embedding_dimension <= 0
            ))
            or not self.index_revision
            or not self.collection
            or not self.vector_text_derivation_version
            or not self.semantic_algorithm_version
            or not self.threshold_config_version
        ):
            raise ValueError("shadow semantic retrieval requires pinned vector lineage")
        if not 0 <= self.semantic_weight <= 0.2:
            raise ValueError("semantic weight must be between 0 and 0.2")
        if self.semantic_weight != 0:
            raise ValueError("semantic retrieval must have semantic_weight=0")
        if not 1 <= self.top_k_per_fragment <= 100 or not 1 <= self.top_n_candidates <= 100:
            raise ValueError("semantic retrieval limits must be within 1..100")
        if not self.dense_enabled and not self.sparse_enabled:
            raise ValueError("dense and sparse retrieval cannot both be disabled")
        if self.dense_rrf_weight < 0 or self.sparse_rrf_weight < 0 or self.rrf_k < 1:
            raise ValueError("RRF weights and k are invalid")
        if not 1 <= self.fusion_top_k <= 50 or not 0 <= self.hybrid_threshold <= 1:
            raise ValueError("hybrid TopK or threshold is invalid")
        if not 1 <= self.reranker_top_k <= 50 or self.reranker_top_n not in {10, 20}:
            raise ValueError("reranker must reorder Top-50 into Top-10 or Top-20")
        if self.reranker_top_n > self.reranker_top_k or self.max_latency_ms <= 0:
            raise ValueError("reranker limits or latency threshold are invalid")
        if self.reranker_enabled and self.reranker_model_revision == "reranker.disabled":
            raise ValueError("enabled reranker requires a pinned model revision")
        allowed_targets = {"standard_position", "enterprise_job"}
        if not self.disabled_target_types.issubset(allowed_targets):
            raise ValueError("semantic disabled target types contain an invalid value")
        if any(not item for item in self.disabled_tenant_refs):
            raise ValueError("semantic disabled tenant refs cannot contain empty values")


class SemanticRetrievalService:
    def __init__(
        self,
        embedding: EmbeddingPort,
        vectors: VectorStorePort,
        config: SemanticRetrievalConfig,
        *,
        sparse: SparseRetrievalPort | None = None,
        reranker: RerankerPort | None = None,
        feature_flags: FeatureFlagController | None = None,
        metrics: MetricsCollector | None = None,
    ) -> None:
        self._embedding = embedding
        self._vectors = vectors
        self.config = config
        self._sparse = sparse
        self._reranker = reranker
        self._feature_flags = feature_flags
        self._metrics = metrics or NullMetricsCollector()
        if config.sparse_enabled and sparse is None:
            raise ValueError("sparse retrieval is enabled without an adapter")
        if config.reranker_enabled and reranker is None:
            raise ValueError("reranker is enabled without an adapter")

    @property
    def algorithm_version(self) -> str:
        return self._algorithm_version(
            use_sparse=self.config.sparse_enabled,
            use_reranker=self.config.reranker_enabled,
        )

    def _algorithm_version(self, *, use_sparse: bool, use_reranker: bool) -> str:
        return (
            f"{self.config.semantic_algorithm_version}"
            f"+dense-{int(self.config.dense_enabled)}"
            f"+sparse-{int(use_sparse)}"
            f"+rrf-k{self.config.rrf_k}"
            f"+rrf-w{self.config.dense_rrf_weight:g}-{self.config.sparse_rrf_weight:g}"
            f"+hybrid-top{self.config.fusion_top_k}-t{self.config.hybrid_threshold:g}"
            "+rerank-"
            f"{self.config.reranker_model_revision if use_reranker else 'off'}"
            f"+top{self.config.reranker_top_k}-{self.config.reranker_top_n}"
        )

    @property
    def embedding(self) -> EmbeddingPort:
        return self._embedding

    @property
    def vectors(self) -> VectorStorePort:
        return self._vectors

    @property
    def sparse(self) -> SparseRetrievalPort | None:
        return self._sparse

    @property
    def reranker(self) -> RerankerPort | None:
        return self._reranker

    def scoring_enabled(self, *, tenant_ref: str, user_ref: str | None = None) -> bool:
        return self._feature_flags is None or self._feature_flags.enabled(
            "scoring", tenant_ref=tenant_ref, user_ref=user_ref
        )

    def record_signal(self, stage, signal) -> None:
        if self._feature_flags is not None:
            self._feature_flags.observe(stage, signal)

    def unavailable(self, error_code: str) -> SemanticRetrievalResult:
        component = (
            "embedding"
            if error_code.startswith("EMBEDDING")
            else "qdrant"
            if error_code.startswith(("QDRANT", "VECTOR"))
            else None
        )
        if component is not None:
            self._metrics.increment(
                "matching_semantic_retrieval_total",
                outcome="error",
                component=component,
            )
        return SemanticRetrievalResult(
            status="unavailable",
            error_code=error_code,
            embedding_model=self.config.embedding_model,
            embedding_dimension=self.config.embedding_dimension or None,
            embedding_normalized=True if self.config.embedding_dimension > 0 else None,
            embedding_normalization="l2" if self.config.embedding_dimension > 0 else None,
            vector_representation="dense" if self.config.embedding_dimension > 0 else None,
            vector_similarity="cosine" if self.config.embedding_dimension > 0 else None,
            text_derivation_version=self.config.vector_text_derivation_version,
            embedding_revision=self.config.embedding_revision,
            index_revision=self.config.index_revision,
            collection=self.config.collection,
        )

    def retrieve(
        self,
        *,
        tenant_ref: str,
        target_type: str,
        cv: CVMatchProfile,
        position: PositionMatchProfile,
        user_ref: str | None = None,
    ) -> SemanticRetrievalResult:
        if (
            self.config.mode == "disabled"
            or tenant_ref in self.config.disabled_tenant_refs
            or target_type in self.config.disabled_target_types
            or (
                self._feature_flags is not None
                and not self._feature_flags.enabled(
                    "retrieval", tenant_ref=tenant_ref, user_ref=user_ref
                )
            )
        ):
            return SemanticRetrievalResult(status="disabled")
        if target_type not in {"standard_position", "enterprise_job"}:
            raise ValueError("semantic target_type is invalid")

        started = time.monotonic()
        trace_id = f"semantic-{uuid4()}"
        fragments = tuple(
            item
            for item in fragment_position_profile(
                position, tenant_ref=tenant_ref, target_type=target_type
            )
            if item.fragment_type in _CANDIDATE_TYPES
        )
        if not fragments:
            return self._available((), started, trace_id, target_type, query_count=0)
        by_fragment: dict[str, tuple[float, ...]] = {}
        if self.config.dense_enabled:
            request = EmbeddingRequest(
                tenant_ref=tenant_ref,
                embedding_model=self.config.embedding_model,
                embedding_revision=self.config.embedding_revision,
                dimension=self.config.embedding_dimension,
                text_derivation_version=self.config.vector_text_derivation_version,
                fragments=fragments,
            )
            embedded = self._embedding.embed(request)
            expected_ids = tuple(item.fragment_id for item in fragments)
            if (
                embedded.tenant_ref != request.tenant_ref
                or embedded.request_id != request.request_id
                or embedded.embedding_model != request.embedding_model
                or embedded.embedding_revision != request.embedding_revision
                or embedded.dimension != request.dimension
                or not embedded.normalized
                or embedded.normalization != "l2"
                or embedded.representation != "dense"
                or embedded.similarity != "cosine"
                or embedded.text_derivation_version
                != request.text_derivation_version
                or embedded.fragment_ids != expected_ids
            ):
                raise VectorContractViolation(
                    "EMBEDDING_RESPONSE_MISMATCH",
                    "embedding result does not match the semantic retrieval request",
                )
            by_fragment = dict(zip(embedded.fragment_ids, embedded.vectors, strict=True))
        records: dict[tuple[str, str], dict[str, object]] = {}
        use_sparse = self.config.sparse_enabled and (
            not self.config.dense_enabled
            or self._feature_flags is None
            or self._feature_flags.enabled("hybrid", tenant_ref=tenant_ref, user_ref=user_ref)
        )
        use_hybrid = self.config.dense_enabled and use_sparse
        use_reranker = self.config.reranker_enabled and (
            self._feature_flags is None
            or self._feature_flags.enabled(
                "reranker", tenant_ref=tenant_ref, user_ref=user_ref
            )
        )
        for query_fragment in fragments:
            filters = VectorFilter(
                profile_version=cv.profile_version,
                fragment_types=_CANDIDATE_TYPES[query_fragment.fragment_type],
                source_ids=(cv.cv_id,),
                target_types=("candidate_cv",),
            )
            if self.config.dense_enabled:
                hits = self._vectors.search(
                    VectorQuery(
                        tenant_ref=tenant_ref,
                        embedding=by_fragment[query_fragment.fragment_id],
                        embedding_model=self.config.embedding_model,
                        embedding_revision=self.config.embedding_revision,
                        text_derivation_version=self.config.vector_text_derivation_version,
                        index_revision=self.config.index_revision,
                        collection=self.config.collection,
                        dimension=self.config.embedding_dimension,
                        filter=filters,
                        top_k=self.config.top_k_per_fragment,
                    )
                )
                for rank, hit in enumerate(hits, 1):
                    self._validate_hit(hit, tenant_ref, cv, query_fragment.fragment_type)
                    records[(query_fragment.fragment_id, hit.fragment.fragment_id)] = {
                        "query": query_fragment,
                        "fragment": hit.fragment,
                        "dense_rank": rank,
                        "dense_score": hit.score,
                        "sparse_rank": None,
                        "sparse_score": None,
                    }
            if use_sparse and self._sparse is not None:
                sparse_hits = self._sparse.search(
                    SparseQuery(
                        tenant_ref=tenant_ref,
                        fragment=query_fragment,
                        filter=filters,
                        index_revision=self.config.index_revision,
                        top_k=self.config.top_k_per_fragment,
                    )
                )
                for rank, hit in enumerate(sparse_hits, 1):
                    self._validate_hit(hit, tenant_ref, cv, query_fragment.fragment_type)
                    if (
                        not hit.active
                        or hit.superseded
                        or hit.profile_version != hit.fragment.source_profile_id
                        or hit.source_version != hit.fragment.source_version
                        or hit.index_revision != self.config.index_revision
                    ):
                        self.record_signal("hybrid", "stale_index")
                        raise VectorContractViolation(
                            "SPARSE_LINEAGE_MISMATCH",
                            "sparse hit does not match the requested active lineage",
                        )
                    record = records.setdefault(
                        (query_fragment.fragment_id, hit.fragment.fragment_id),
                        {
                            "query": query_fragment,
                            "fragment": hit.fragment,
                            "dense_rank": None,
                            "dense_score": None,
                            "sparse_rank": None,
                            "sparse_score": None,
                        },
                    )
                    record.update({"sparse_rank": rank, "sparse_score": hit.score})
        evidence = self._fuse_records(
            records,
            trace_id,
            use_sparse=use_sparse,
            use_hybrid=use_hybrid,
            limit=(
                self.config.reranker_top_k
                if use_reranker
                else self.config.fusion_top_k
                if use_hybrid
                else None
            ),
        )
        evidence, reranker_status, degradation_reason = self._rerank(
            evidence, enabled=use_reranker, tenant_ref=tenant_ref
        )
        return self._available(
            evidence,
            started,
            trace_id,
            target_type,
            query_count=len(fragments),
            reranker_status=reranker_status,
            degradation_reason=degradation_reason,
            algorithm_version=self._algorithm_version(
                use_sparse=use_sparse,
                use_reranker=use_reranker,
            ),
        )

    def _validate_hit(self, hit, tenant_ref, cv, query_fragment_type) -> None:
        if (
            hit.tenant_ref != tenant_ref
            or hit.fragment.tenant_ref != tenant_ref
            or hit.fragment.target_type != "candidate_cv"
            or hit.fragment.source_id != cv.cv_id
            or hit.fragment.source_profile_id != cv.profile_version
            or hit.fragment.fragment_type not in _CANDIDATE_TYPES[query_fragment_type]
        ):
            self.record_signal(
                "retrieval",
                "cross_tenant_hit"
                if hit.tenant_ref != tenant_ref or hit.fragment.tenant_ref != tenant_ref
                else "error",
            )
            raise VectorContractViolation(
                "VECTOR_FILTER_VIOLATION",
                "retriever returned a hit outside tenant/version/active filters",
            )

    def _fuse_records(
        self,
        records: dict[tuple[str, str], dict[str, object]],
        trace_id: str,
        *,
        use_sparse: bool,
        use_hybrid: bool,
        limit: int | None,
    ) -> tuple[SemanticRetrievalEvidence, ...]:
        scored: list[tuple[float, tuple[str, str], dict[str, object]]] = []
        for key, record in records.items():
            dense_rank = record["dense_rank"]
            sparse_rank = record["sparse_rank"]
            rrf = (
                self.config.dense_rrf_weight / (self.config.rrf_k + dense_rank)
                if isinstance(dense_rank, int)
                else 0.0
            ) + (
                self.config.sparse_rrf_weight / (self.config.rrf_k + sparse_rank)
                if isinstance(sparse_rank, int)
                else 0.0
            )
            scored.append((rrf, key, record))
        scored.sort(key=lambda item: (-item[0], item[1]))
        max_rrf = scored[0][0] if scored else 1.0
        result = []
        for final_rank, (rrf, _key, record) in enumerate(scored, 1):
            query = record["query"]
            fragment = record["fragment"]
            dense_score = record["dense_score"]
            sparse_score = record["sparse_score"]
            retrieval_score = (
                float(dense_score)
                if self.config.dense_enabled and not use_sparse
                else float(sparse_score)
                if not self.config.dense_enabled and use_sparse
                else (rrf / max_rrf if max_rrf else 0.0)
            )
            result.append(
                SemanticRetrievalEvidence(
                    query_fragment_id=query.fragment_id,
                    candidate_fragment_id=fragment.fragment_id,
                    query_fragment_type=query.fragment_type,
                    candidate_fragment_type=fragment.fragment_type,
                    candidate_source_id=fragment.source_id,
                    similarity=(
                        float(dense_score)
                        if dense_score is not None
                        else float(sparse_score)
                    ),
                    rank=final_rank,
                    dense_rank=record["dense_rank"],
                    sparse_rank=record["sparse_rank"],
                    rrf_score=rrf,
                    retrieval_score=retrieval_score,
                    final_rank=final_rank,
                    evidence_ref=fragment.evidence_ref,
                    position_evidence_ref=query.evidence_ref,
                    profile_version=fragment.source_profile_id,
                    embedding_model=self.config.embedding_model,
                    embedding_revision=self.config.embedding_revision,
                    embedding_dimension=self.config.embedding_dimension,
                    embedding_normalized=True,
                    embedding_normalization="l2",
                    vector_representation="dense",
                    vector_similarity="cosine",
                    text_derivation_version=self.config.vector_text_derivation_version,
                    index_revision=self.config.index_revision,
                    collection=self.config.collection,
                    retrieval_trace_id=trace_id,
                )
            )
        if use_hybrid:
            result = [
                item
                for item in result[: limit or len(result)]
                if (item.retrieval_score or 0.0) >= self.config.hybrid_threshold
            ]
        elif limit is not None:
            result = result[:limit]
        return tuple(result)

    def _rerank(self, evidence, *, enabled, tenant_ref):
        if not enabled or not evidence or self._reranker is None:
            return evidence, "disabled", None
        inputs = evidence[: self.config.reranker_top_k]
        items = tuple(
            RerankItem(
                retrieval_item_id=f"{item.query_fragment_id}:{item.candidate_fragment_id}",
                candidate_fragment_id=item.candidate_fragment_id,
                query_text=item.position_evidence_ref.quote,
                candidate_text=item.evidence_ref.quote,
                retrieval_score=item.retrieval_score or 0.0,
            )
            for item in inputs
        )
        try:
            scores = self._reranker.rerank(
                RerankRequest(
                    tenant_ref=tenant_ref,
                    model_revision=self.config.reranker_model_revision,
                    items=items,
                    top_n=self.config.reranker_top_n,
                )
            )
        except TimeoutError:
            return evidence[: self.config.reranker_top_n], "degraded", "RERANKER_TIMEOUT"
        except VectorContractViolation:
            return (
                evidence[: self.config.reranker_top_n],
                "degraded",
                "RERANKER_DEPENDENCY_INVALID",
            )
        allowed = {item.retrieval_item_id for item in items}
        expected_count = min(len(items), self.config.reranker_top_n)
        if (
            len(scores) != expected_count
            or len(scores) != len({item.retrieval_item_id for item in scores})
            or any(item.retrieval_item_id not in allowed for item in scores)
        ):
            return (
                evidence[: self.config.reranker_top_n],
                "degraded",
                "RERANKER_RESPONSE_INCOMPLETE",
            )
        by_id = {item.retrieval_item_id: item.score for item in scores}
        ranked = sorted(
            (
                item
                for item in inputs
                if f"{item.query_fragment_id}:{item.candidate_fragment_id}" in by_id
            ),
            key=lambda item: (
                -by_id.get(f"{item.query_fragment_id}:{item.candidate_fragment_id}", -1.0),
                item.final_rank or item.rank,
            ),
        )[: self.config.reranker_top_n]
        return tuple(
            item.model_copy(
                update={
                    "rerank_score": by_id.get(
                        f"{item.query_fragment_id}:{item.candidate_fragment_id}"
                    ),
                    "final_rank": rank,
                    "reranker_model_revision": self.config.reranker_model_revision,
                }
            )
            for rank, item in enumerate(ranked, 1)
        ), "applied", None

    def _available(
        self,
        evidence: tuple[SemanticRetrievalEvidence, ...],
        started: float,
        trace_id: str,
        target_type: str,
        query_count: int,
        reranker_status: str = "disabled",
        degradation_reason: str | None = None,
        algorithm_version: str | None = None,
    ) -> SemanticRetrievalResult:
        grouped: dict[str, list[SemanticRetrievalEvidence]] = {}
        for item in evidence:
            grouped.setdefault(item.candidate_source_id, []).append(item)
        candidates = tuple(
            sorted(
                (
                    SemanticCandidate(
                        candidate_source_id=source_id,
                        score=(
                            sum(
                                (
                                    item.rerank_score
                                    if item.rerank_score is not None
                                    else item.retrieval_score or 0.0
                                )
                                for item in {
                                    evidence.query_fragment_id: evidence
                                    for evidence in sorted(
                                        items,
                                        key=lambda evidence: (
                                            evidence.rerank_score
                                            if evidence.rerank_score is not None
                                            else evidence.retrieval_score or 0.0
                                        ),
                                    )
                                }.values()
                            )
                            / query_count
                        ),
                        evidence=tuple(
                            sorted(items, key=lambda item: item.final_rank or item.rank)
                        ),
                        retrieval_score=max(item.retrieval_score or 0.0 for item in items),
                        rerank_score=max(
                            (item.rerank_score for item in items if item.rerank_score is not None),
                            default=None,
                        ),
                        final_rank=min(item.final_rank or item.rank for item in items),
                        reranker_model_revision=(
                            self.config.reranker_model_revision
                            if reranker_status == "applied"
                            else None
                        ),
                        degraded=reranker_status == "degraded",
                        degradation_reason=degradation_reason,
                    )
                    for source_id, items in grouped.items()
                ),
                key=lambda item: (-item.score, item.candidate_source_id),
            )[: self.config.top_n_candidates]
        )
        latency = (time.monotonic() - started) * 1000
        if self._feature_flags is not None:
            signals = []
            if not evidence:
                signals.append("empty_retrieval")
            if latency > self.config.max_latency_ms:
                signals.append("latency")
            self._feature_flags.observe("retrieval", *signals)
            if reranker_status == "degraded":
                self._feature_flags.observe("reranker", "error")
        self._metrics.increment(
            "matching_semantic_retrieval_total",
            outcome="success",
            target_type=target_type,
        )
        self._metrics.increment(
            "matching_semantic_hits_total", value=len(evidence), target_type=target_type
        )
        if not evidence:
            self._metrics.increment(
                "matching_semantic_empty_retrieval_total", target_type=target_type
            )
        self._metrics.observe(
            "matching_semantic_retrieval_duration_seconds",
            latency / 1000,
            target_type=target_type,
        )
        return SemanticRetrievalResult(
            status="available",
            candidates=candidates,
            hit_count=len(evidence),
            latency_ms=latency,
            embedding_revision=self.config.embedding_revision,
            embedding_model=self.config.embedding_model,
            embedding_dimension=self.config.embedding_dimension,
            embedding_normalized=True,
            embedding_normalization="l2",
            vector_representation="dense",
            vector_similarity="cosine",
            text_derivation_version=self.config.vector_text_derivation_version,
            index_revision=self.config.index_revision,
            collection=self.config.collection,
            retrieval_trace_id=trace_id,
            algorithm_version=algorithm_version or self.algorithm_version,
            reranker_model_revision=(
                self.config.reranker_model_revision if reranker_status == "applied" else None
            ),
            reranker_status=reranker_status,
            degradation_reason=degradation_reason,
        )


def semantic_explanations(
    evidence: tuple[SemanticRetrievalEvidence, ...],
) -> tuple[SemanticMatchExplanation, ...]:
    dimensions = {
        "skill_context": "skill_semantic_match",
        "project": "project_semantic_match",
        "project_responsibility": "project_semantic_match",
        "scenario_evidence": "scenario_semantic_match",
    }
    return tuple(
        SemanticMatchExplanation(
            dimension=dimensions.get(
                item.candidate_fragment_type, "responsibility_semantic_match"
            ),
            score=item.similarity,
            position_text=item.position_evidence_ref.quote,
            resume_evidence=item.evidence_ref.quote,
            evidence_ref=(
                f"{item.evidence_ref.source_id}:"
                f"{item.evidence_ref.start}:{item.evidence_ref.end}"
            ),
            embedding_revision=item.embedding_revision,
        )
        for item in evidence
    )


__all__ = [
    "SemanticRetrievalConfig",
    "SemanticRetrievalService",
    "semantic_explanations",
]
