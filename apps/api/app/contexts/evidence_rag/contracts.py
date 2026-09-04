from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from jobgraph_contracts.rag import EvidenceTypeScope, RAGVersionScope


EVIDENCE_RAG_INDEX_CONTRACT_VERSION = "evidence-rag-index.v1"
EvidenceAlignment = Literal["exact", "normalized_exact", "unresolved"]
EvidenceEntailmentRelation = Literal["support", "contradict", "insufficient"]
EvidenceQueryDepth = Literal["fact", "compare", "overview"]


class EvidenceRagError(RuntimeError):
    """Stable error raised by the Evidence RAG application and adapters."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _require_version_identity(
    graph_version_id: int | None,
    graph_version: str | None,
    business_version: str | None,
) -> None:
    identities = [
        value
        for value in (graph_version_id, graph_version, business_version)
        if value is not None
    ]
    if len(identities) != 1:
        raise ValueError(
            "exactly one of graph_version_id, graph_version, or business_version is required"
        )


def _require_index_version_identity(
    graph_version_id: int | None,
    graph_version: str | None,
    business_version: str | None,
) -> None:
    if business_version is not None:
        if graph_version_id is not None or graph_version is not None:
            raise ValueError(
                "business_version cannot be combined with "
                "graph_version_id or graph_version"
            )
        return
    if graph_version_id is None and graph_version is None:
        raise ValueError(
            "at least one of graph_version_id or graph_version is required"
        )


@dataclass(frozen=True)
class EvidenceRagRecord:
    evidence_id: str
    business_object_type: str
    business_object_id: str
    evidence_type: EvidenceTypeScope
    source_object_type: str
    source_object_id: str
    source_document_id: str
    source_version: str
    text: str
    tenant_ref: str
    permission_scope: str
    business_object_name: str | None = None
    quote: str | None = None
    location_start: int | None = None
    location_end: int | None = None
    occurrence_index: int | None = None
    alignment: EvidenceAlignment = "unresolved"
    graph_version_id: int | None = None
    graph_version: str | None = None
    business_version: str | None = None

    def __post_init__(self) -> None:
        required = (
            self.evidence_id,
            self.business_object_type,
            self.business_object_id,
            self.source_object_type,
            self.source_object_id,
            self.source_document_id,
            self.source_version,
            self.tenant_ref,
            self.permission_scope,
        )
        if any(not str(value).strip() for value in required):
            raise ValueError("Evidence RAG record fields must not be empty")
        if not self.text.strip():
            raise ValueError("Evidence RAG record text must not be empty")
        _require_index_version_identity(
            self.graph_version_id, self.graph_version, self.business_version
        )
        if (self.location_start is None) != (self.location_end is None):
            raise ValueError("evidence location start and end must be supplied together")
        if (
            self.location_start is not None
            and self.location_end is not None
            and self.location_end < self.location_start
        ):
            raise ValueError("evidence location end must not precede start")
        if not self.quote and (
            self.location_start is None or self.location_end is None
        ):
            raise ValueError("Evidence RAG record requires quote or location span")
        if not self.quote and self.alignment == "exact":
            raise ValueError("exact Evidence RAG record requires quote")


@dataclass(frozen=True)
class EvidenceRagQuery:
    query_text: str
    business_object_type: str
    business_object_id: str
    evidence_types: tuple[str, ...]
    tenant_ref: str
    permission_scope: str
    business_object_label: str | None = None
    graph_version_id: int | None = None
    graph_version: str | None = None
    business_version: str | None = None
    version_scope: RAGVersionScope = "single_object"
    top_k: int = 5
    query_depth: EvidenceQueryDepth = "fact"
    business_object_ids: tuple[str, ...] | None = None
    business_object_versions: tuple[tuple[str, int], ...] | None = None
    retrieval_text: str | None = None

    def __post_init__(self) -> None:
        if not self.query_text.strip():
            raise ValueError("RAG query text must not be empty")
        if not self.business_object_type.strip() or not self.business_object_id.strip():
            raise ValueError("RAG query business object must not be empty")
        if not self.evidence_types:
            raise ValueError("RAG query evidence types must not be empty")
        if not self.tenant_ref.strip() or not self.permission_scope.strip():
            raise ValueError("RAG query tenant and permission scope must not be empty")
        if self.top_k <= 0:
            raise ValueError("RAG query top_k must be positive")
        if self.version_scope not in ("single_object", "multi_object"):
            raise ValueError("RAG query version_scope is invalid")
        if self.business_object_ids is not None and not self.business_object_ids:
            raise ValueError("RAG query business object ids must not be empty")
        if self.business_object_ids is not None and len(
            set(self.business_object_ids)
        ) != len(self.business_object_ids):
            raise ValueError("RAG query business object ids must be unique")
        if self.business_object_versions is not None:
            if not self.business_object_versions:
                raise ValueError("RAG query business object versions must not be empty")
            if len({object_id for object_id, _ in self.business_object_versions}) != len(
                self.business_object_versions
            ):
                raise ValueError("RAG query business object versions must be unique")
            if any(
                not object_id or version_id <= 0
                for object_id, version_id in self.business_object_versions
            ):
                raise ValueError("RAG query business object versions are invalid")
        identities = (
            self.graph_version_id,
            self.graph_version,
            self.business_version,
        )
        if self.version_scope == "multi_object":
            if any(identity is not None for identity in identities):
                raise ValueError(
                    "multi_object RAG query must not declare a global version identity"
                )
            if self.business_object_ids is None or len(self.business_object_ids) < 2:
                raise ValueError(
                    "multi_object RAG query requires at least two business objects"
                )
            if self.business_object_versions is None:
                raise ValueError(
                    "multi_object RAG query requires per-object graph versions"
                )
            requested_ids = {
                object_id for object_id, _ in self.business_object_versions
            }
            if requested_ids != set(self.business_object_ids):
                raise ValueError(
                    "multi_object RAG query ids and graph versions must match"
                )
            if self.business_object_id not in requested_ids:
                raise ValueError(
                    "multi_object RAG query must include the primary business object"
                )
        else:
            if self.business_object_ids is not None and len(self.business_object_ids) > 1:
                raise ValueError(
                    "multiple business objects require version_scope=multi_object"
                )
            if self.business_object_versions is not None:
                raise ValueError(
                    "per-object graph versions require version_scope=multi_object"
                )
            _require_version_identity(*identities)


@dataclass(frozen=True)
class EvidenceRagHit:
    evidence_id: str
    source_object_type: str
    source_object_id: str
    source_document_id: str
    source_version: str
    score: float
    quote: str | None
    location_start: int | None
    location_end: int | None
    occurrence_index: int | None
    alignment: EvidenceAlignment
    graph_version_id: int | None
    graph_version: str | None
    business_version: str | None
    tenant_ref: str
    permission_scope: str
    business_object_id: str = ""
    business_object_name: str | None = None
    highlight_text: str | None = None


@dataclass(frozen=True)
class EvidenceCitationQuery:
    evidence_id: str
    source_version: str
    graph_version_id: int | None = None
    graph_version: str | None = None
    business_version: str | None = None

    def __post_init__(self) -> None:
        if not self.evidence_id.strip() or not self.source_version.strip():
            raise ValueError("citation Evidence and source version must not be empty")
        identities = tuple(
            value
            for value in (
                self.graph_version_id,
                self.graph_version,
                self.business_version,
            )
            if value is not None
        )
        if len(identities) > 1:
            raise ValueError("citation accepts at most one graph or business version")


@dataclass(frozen=True)
class EvidenceCitationTarget:
    route: str
    resource_id: str

    def __post_init__(self) -> None:
        if not self.route.startswith("/") or not self.resource_id.strip():
            raise ValueError("citation target route and resource id are required")


class EvidenceCitationTargetPort(Protocol):
    def resolve(
        self, *, actor_id: str, actor_role: str, hit: EvidenceRagHit
    ) -> EvidenceCitationTarget: ...


@dataclass(frozen=True)
class EvidenceRagLlmAnswer:
    answer: str
    supported: bool
    used_evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceEntailment:
    claim: str
    relation: EvidenceEntailmentRelation
    used_evidence_ids: tuple[str, ...]
    reason: str
    dimension: str | None = None

    def __post_init__(self) -> None:
        if not self.claim.strip() or not self.reason.strip():
            raise ValueError("entailment claim and reason must not be empty")
        if self.relation not in {"support", "contradict", "insufficient"}:
            raise ValueError("entailment relation is invalid")


class EvidenceRagEmbeddingPort(Protocol):
    def embed(self, text: str) -> list[float]: ...
    def embed_many(
        self, texts: tuple[str, ...]
    ) -> tuple[tuple[float, ...], ...]: ...
    def model_id(self) -> str: ...
    def model_revision(self) -> str: ...
    def dimension(self) -> int: ...


class EvidenceRagStorePort(Protocol):
    def upsert(self, record: EvidenceRagRecord, vector: list[float]) -> None: ...
    def upsert_many(
        self,
        records: tuple[EvidenceRagRecord, ...],
        vectors: tuple[tuple[float, ...], ...],
    ) -> None: ...
    def count(
        self,
        *,
        business_object_type: str,
        business_object_id: str,
        graph_version_id: int | None = None,
        graph_version: str | None = None,
        business_version: str | None = None,
        active_only: bool = True,
    ) -> int: ...
    def search(
        self, query: EvidenceRagQuery, vector: list[float]
    ) -> tuple[EvidenceRagHit, ...]: ...
    def citations(self, query: EvidenceCitationQuery) -> tuple[EvidenceRagHit, ...]: ...
    def deactivate(
        self,
        *,
        tenant_ref: str,
        permission_scope: str,
        source_object_type: str | None = None,
        source_object_id: str | None = None,
        source_document_id: str | None = None,
        source_version: str | None = None,
    ) -> None: ...
    def delete(
        self,
        *,
        tenant_ref: str,
        permission_scope: str,
        source_object_type: str | None = None,
        source_object_id: str | None = None,
        source_document_id: str | None = None,
        source_version: str | None = None,
    ) -> None: ...


class EvidenceRagLlmPort(Protocol):
    def entailment(
        self, *, query_text: str, evidence_text: str
    ) -> tuple[EvidenceEntailment, ...]: ...
    def judge_and_answer(
        self,
        *,
        query_text: str,
        evidence_text: str,
        query_depth: EvidenceQueryDepth = "fact",
        business_object_ids: tuple[str, ...] = (),
        objects_with_evidence: tuple[str, ...] = (),
    ) -> tuple[tuple[EvidenceEntailment, ...], EvidenceRagLlmAnswer]: ...
    def answer(
        self,
        *,
        query_text: str,
        evidence_text: str,
        entailment_text: str | None = None,
    ) -> EvidenceRagLlmAnswer: ...
    def provider(self) -> str: ...
    def model(self) -> str: ...
    def model_version(self) -> str: ...


__all__ = [
    "EVIDENCE_RAG_INDEX_CONTRACT_VERSION",
    "EvidenceAlignment",
    "EvidenceEntailment",
    "EvidenceEntailmentRelation",
    "EvidenceQueryDepth",
    "EvidenceCitationQuery",
    "EvidenceCitationTarget",
    "EvidenceCitationTargetPort",
    "EvidenceRagEmbeddingPort",
    "EvidenceRagError",
    "EvidenceRagHit",
    "EvidenceRagLlmPort",
    "EvidenceRagLlmAnswer",
    "EvidenceRagQuery",
    "EvidenceRagRecord",
    "EvidenceRagStorePort",
]
