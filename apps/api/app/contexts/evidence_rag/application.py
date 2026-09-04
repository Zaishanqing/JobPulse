from __future__ import annotations

from dataclasses import dataclass, replace
import logging
from typing import Callable, Mapping
from urllib.parse import urlencode

from pydantic import ValidationError

from app.contexts.evidence_rag.contracts import (
    EVIDENCE_RAG_INDEX_CONTRACT_VERSION,
    EvidenceCitationQuery,
    EvidenceCitationTargetPort,
    EvidenceEntailment,
    EvidenceRagLlmAnswer,
    EvidenceRagEmbeddingPort,
    EvidenceRagError,
    EvidenceQueryDepth,
    EvidenceRagHit,
    EvidenceRagLlmPort,
    EvidenceRagQuery,
    EvidenceRagRecord,
    EvidenceRagStorePort,
)
from app.core.request_context import get_trace_id
from app.domain.accounts import AccountActor
from app.domain.errors import PermissionDenied
from app.domain.json_types import FrozenJsonObject, FrozenJsonValue
from app.profile_index_events import PLATFORM_PUBLIC_TENANT_REF
from jobgraph_contracts.rag import (
    EvidenceEntailmentV1,
    EvidenceRAGCoverageV1,
    EvidenceRAGResponseV1,
    PermissionContextV1,
    RAGErrorV1,
    RAGEvidenceReferenceV1,
    RAGVersionScope,
)


PLATFORM_PERMISSION_SCOPE = "platform:public"
PERSONAL_SCOPE_PREFIX = "personal:"
ENTERPRISE_SCOPE_PREFIX = "enterprise:"
INTERNAL_RAG_ROLES = frozenset({"admin", "developer"})
RAG_INDEX_BATCH_SIZE = 16
RagScopePayload = Mapping[str, FrozenJsonValue]
RagIndexStatus = Mapping[str, FrozenJsonValue]
logger = logging.getLogger(__name__)

QUERY_DEPTH_TOP_K: dict[EvidenceQueryDepth, int] = {
    "fact": 5,
    "compare": 8,
    "overview": 10,
}
QUERY_DEPTH_MAX_K = 20
QUERY_DEPTH_OVERVIEW_HINTS = (
    "哪些",
    "主要",
    "整体",
    "全面",
    "总结",
    "分析",
    "趋势",
    "发展",
    "能力要求",
    "技能要求",
    "常见",
    "普遍",
    "分布",
)
QUERY_DEPTH_COMPARE_HINTS = (
    "比较",
    "区别",
    "差异",
    "相比",
    "哪个",
    "分别",
)
QUERY_DEPTH_MIN_RETAINED = 3
QUERY_DEPTH_SCORE_DECAY_RATIO: dict[EvidenceQueryDepth, float] = {
    "fact": 0.70,
    "compare": 0.60,
    "overview": 0.55,
}
QUERY_DEPTH_SCORE_DROP: dict[EvidenceQueryDepth, float] = {
    "fact": 0.12,
    "compare": 0.16,
    "overview": 0.20,
}
MULTI_OBJECT_PER_OBJECT_K: dict[EvidenceQueryDepth, int] = {
    "fact": 2,
    "compare": 3,
    "overview": 3,
}
MULTI_OBJECT_MAX_HITS = 40
MULTI_OBJECT_CONTEXT_BUDGETS: tuple[tuple[int, int], ...] = (
    (2, 12_000),
    (4, 12_000),
    (8, 18_000),
    (15, 24_000),
)
MULTI_OBJECT_MAX_CONTEXT_CHARS = 24_000


def _response_version_kwargs(
    version: Mapping[str, object],
    *,
    scope: RAGVersionScope | None = None,
) -> dict[str, object]:
    response_scope = scope
    if response_scope is None:
        raw_scope = version.get("version_scope")
        if raw_scope in ("single_object", "multi_object"):
            response_scope = raw_scope
        else:
            business_objects = version.get("business_objects")
            response_scope = (
                "multi_object"
                if isinstance(business_objects, (list, tuple))
                and len(business_objects) > 1
                else "single_object"
            )
    return {
        "version_scope": response_scope,
        "graph_version_id": (
            None
            if response_scope == "multi_object"
            else version.get("graph_version_id")
        ),
        "graph_version": (
            None if response_scope == "multi_object" else version.get("graph_version")
        ),
        "business_version": (
            None
            if response_scope == "multi_object"
            else version.get("business_version")
        ),
    }


def _failed(
    code: str,
    message: str,
    permission: PermissionContextV1,
    version: Mapping[str, object],
) -> EvidenceRAGResponseV1:
    return EvidenceRAGResponseV1(
        contract_version="evidence-rag-response.v1",
        status="failed",
        provider="main-system-bff",
        model="unavailable",
        model_version="unavailable",
        trace_id=get_trace_id(),
        error=RAGErrorV1(code=code, message=message),
        permission=permission,
        **_response_version_kwargs(version),
    )


def _insufficient(
    permission: PermissionContextV1,
    version: Mapping[str, object],
    *,
    code: str = "EVIDENCE_NOT_FOUND",
    message: str = "no active Evidence matches the query filters",
    entailment: tuple[EvidenceEntailment, ...] = (),
    coverage: EvidenceRAGCoverageV1 | None = None,
) -> EvidenceRAGResponseV1:
    return EvidenceRAGResponseV1(
        contract_version="evidence-rag-response.v1",
        status="insufficient_evidence",
        provider="evidence_rag",
        model="unavailable",
        model_version="unavailable",
        trace_id=get_trace_id(),
        error=RAGErrorV1(
            code=code,
            message=message,
        ),
        entailment=[_entailment_model(item) for item in entailment],
        coverage=coverage,
        permission=permission,
        **_response_version_kwargs(version),
    )


def _reference(hit: EvidenceRagHit) -> RAGEvidenceReferenceV1:
    return RAGEvidenceReferenceV1(
        evidence_id=hit.evidence_id,
        business_object_id=hit.business_object_id or None,
        source_object_type=hit.source_object_type,
        source_object_id=hit.source_object_id,
        source_document_id=hit.source_document_id,
        quote=hit.quote,
        location_start=hit.location_start,
        location_end=hit.location_end,
        occurrence_index=hit.occurrence_index,
        alignment=hit.alignment,
        graph_version_id=hit.graph_version_id,
        graph_version=hit.graph_version,
        business_version=hit.business_version,
        source_version=hit.source_version,
        retrieval_score=hit.score,
        tenant_ref=hit.tenant_ref,
        permission_scope=hit.permission_scope,
    )


def _entailment_model(item: EvidenceEntailment) -> EvidenceEntailmentV1:
    return EvidenceEntailmentV1(
        claim=item.claim,
        relation=item.relation,
        used_evidence_ids=list(item.used_evidence_ids),
        reason=item.reason,
        dimension=item.dimension,
    )


@dataclass(frozen=True)
class ManageEvidenceRag:
    embedding: EvidenceRagEmbeddingPort | None
    store: EvidenceRagStorePort | None
    llm: EvidenceRagLlmPort | None
    enabled: bool
    permission_resolver: Callable[[AccountActor], tuple[str, str]]
    profile_text_provider: Callable[[RagScopePayload], str | None] | None = None
    index_status_provider: Callable[[RagScopePayload], RagIndexStatus | None] = None
    top_k: int = 5
    max_context_chars: int = 8000
    min_score: float = 0.0
    citation_target_resolver: EvidenceCitationTargetPort | None = None
    multi_object_max_hits: int = MULTI_OBJECT_MAX_HITS
    multi_object_max_context_chars: int = MULTI_OBJECT_MAX_CONTEXT_CHARS

    def permission_for(self, actor: AccountActor) -> PermissionContextV1:
        tenant_ref, scope = self.permission_resolver(actor)
        return PermissionContextV1(
            user_id=actor.account_id,
            tenant_ref=tenant_ref,
            permission_scope=scope,
        )

    def resolve_citation(
        self, actor: AccountActor, payload: FrozenJsonObject
    ) -> FrozenJsonObject:
        if not self.enabled or self.store is None or self.citation_target_resolver is None:
            raise EvidenceRagError(
                "RAG_EVIDENCE_DISABLED", "Evidence citation resolver is disabled"
            )
        try:
            permission = self.permission_for(actor)
            query = EvidenceCitationQuery(
                evidence_id=str(payload["evidence_id"]),
                source_version=str(payload["source_version"]),
                graph_version_id=(
                    int(payload["graph_version_id"])
                    if payload.get("graph_version_id") is not None
                    else None
                ),
                graph_version=self._optional(payload, "graph_version"),
                business_version=self._optional(payload, "business_version"),
            )
        except PermissionDenied as exc:
            raise EvidenceRagError("CITATION_PERMISSION_DENIED", str(exc)) from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise EvidenceRagError("CITATION_REQUEST_INVALID", str(exc)) from exc
        candidates = self.store.citations(query)
        if not candidates:
            raise EvidenceRagError(
                "CITATION_NOT_FOUND", "Evidence citation does not exist or is inactive"
            )
        hits = tuple(
            item for item in candidates if self._citation_version_matches(item, query)
        )
        if not hits:
            raise EvidenceRagError(
                "CITATION_VERSION_INVALID",
                "Evidence citation or requested version is no longer active",
            )
        hit = next(
            (
                item
                for item in hits
                if (
                    item.tenant_ref == PLATFORM_PUBLIC_TENANT_REF
                    and item.permission_scope == PLATFORM_PERMISSION_SCOPE
                )
                or (
                    item.tenant_ref == permission.tenant_ref
                    and item.permission_scope == permission.permission_scope
                )
            ),
            None,
        )
        if hit is None:
            raise EvidenceRagError(
                "CITATION_PERMISSION_DENIED",
                "Evidence citation is outside the current tenant or permission scope",
            )
        target = self.citation_target_resolver.resolve(
            actor_id=actor.account_id,
            actor_role=actor.role,
            hit=hit,
        )
        version_id: str | int = (
            hit.graph_version_id
            if hit.graph_version_id is not None
            else hit.graph_version or hit.business_version or hit.source_version
        )
        query_params: dict[str, str] = {
            "citationEvidenceId": hit.evidence_id,
            "citationSourceVersion": hit.source_version,
        }
        if hit.graph_version_id is not None:
            query_params["citationGraphVersionId"] = str(hit.graph_version_id)
        elif hit.graph_version is not None:
            query_params["citationGraphVersion"] = hit.graph_version
        elif hit.business_version is not None:
            query_params["citationBusinessVersion"] = hit.business_version
        separator = "&" if "?" in target.route else "?"
        return FrozenJsonObject(
            {
                "contract_version": "evidence-citation-resolution.v1",
                "target_route": f"{target.route}{separator}{urlencode(query_params)}",
                "resource_id": target.resource_id,
                "version_id": version_id,
                "evidence_id": hit.evidence_id,
                "start": hit.location_start,
                "end": hit.location_end,
                "highlight_text": hit.highlight_text or hit.quote or "",
                "source_object_type": hit.source_object_type,
                "source_object_id": hit.source_object_id,
                "source_document_id": hit.source_document_id,
                "source_version": hit.source_version,
                "graph_version_id": hit.graph_version_id,
                "graph_version": hit.graph_version,
                "business_version": hit.business_version,
            }
        )

    @staticmethod
    def _citation_version_matches(
        hit: EvidenceRagHit, query: EvidenceCitationQuery
    ) -> bool:
        if hit.source_version != query.source_version:
            return False
        if query.graph_version_id is not None:
            return hit.graph_version_id == query.graph_version_id
        if query.graph_version is not None:
            return hit.graph_version == query.graph_version
        if query.business_version is not None:
            return hit.business_version == query.business_version
        return True

    def index(
        self, actor: AccountActor, items: tuple[FrozenJsonObject, ...]
    ) -> FrozenJsonObject:
        self._require_internal(actor)
        if not self.enabled or self.store is None:
            raise EvidenceRagError(
                "RAG_EVIDENCE_DISABLED", "Evidence RAG indexing is disabled"
            )
        records: list[EvidenceRagRecord] = []
        for item in items:
            try:
                records.append(EvidenceRagRecord(**dict(item)))
            except (TypeError, ValueError) as exc:
                raise EvidenceRagError(
                    "EVIDENCE_RAG_RECORD_INVALID", str(exc)
                ) from exc
        for start in range(0, len(records), RAG_INDEX_BATCH_SIZE):
            batch = records[start : start + RAG_INDEX_BATCH_SIZE]
            vectors = self.embedding.embed_many(
                tuple(record.text for record in batch)
            )
            if len(vectors) != len(batch):
                raise EvidenceRagError(
                    "EVIDENCE_RAG_RECORD_INVALID",
                    "embedding count must match the record count",
                )
            self.store.upsert_many(tuple(batch), vectors)
        return FrozenJsonObject(
            {
                "contract_version": EVIDENCE_RAG_INDEX_CONTRACT_VERSION,
                "indexed_count": len(records),
            }
        )

    def deactivate(
        self, actor: AccountActor, filters: FrozenJsonObject
    ) -> FrozenJsonObject:
        self._require_internal(actor)
        if not self.enabled or self.store is None:
            raise EvidenceRagError(
                "RAG_EVIDENCE_DISABLED", "Evidence RAG invalidation is disabled"
            )
        self.store.deactivate(
            tenant_ref=str(filters["tenant_ref"]),
            permission_scope=str(filters["permission_scope"]),
            source_object_type=self._optional(filters, "source_object_type"),
            source_object_id=self._optional(filters, "source_object_id"),
            source_document_id=self._optional(filters, "source_document_id"),
            source_version=self._optional(filters, "source_version"),
        )
        return FrozenJsonObject({"invalidated": True})

    def delete(
        self, actor: AccountActor, filters: FrozenJsonObject
    ) -> FrozenJsonObject:
        self._require_internal(actor)
        if not self.enabled or self.store is None:
            raise EvidenceRagError(
                "RAG_EVIDENCE_DISABLED", "Evidence RAG deletion is disabled"
            )
        self.store.delete(
            tenant_ref=str(filters["tenant_ref"]),
            permission_scope=str(filters["permission_scope"]),
            source_object_type=self._optional(filters, "source_object_type"),
            source_object_id=self._optional(filters, "source_object_id"),
            source_document_id=self._optional(filters, "source_document_id"),
            source_version=self._optional(filters, "source_version"),
        )
        return FrozenJsonObject({"deleted": True})

    def query(
        self, actor: AccountActor, payload: FrozenJsonObject
    ) -> EvidenceRAGResponseV1:
        try:
            permission = self.permission_for(actor)
        except PermissionDenied as exc:
            return _failed(
                "PERMISSION_DENIED",
                str(exc),
                PermissionContextV1(
                    user_id=actor.account_id,
                    tenant_ref="unavailable",
                    permission_scope="unavailable",
                ),
                payload,
            )
        if not self.enabled or any(
            adapter is None
            for adapter in (self.embedding, self.store, self.llm)
        ):
            return _failed(
                "RAG_EVIDENCE_DISABLED",
                "Evidence RAG query is disabled",
                permission,
                payload,
            )
        candidate_groups: tuple[
            tuple[str, tuple[EvidenceRagHit, ...]], ...
        ] = ()
        retrieval_hits: tuple[EvidenceRagHit, ...] = ()
        try:
            query = self._query(permission, payload)
            vector = self.embedding.embed(
                self._retrieval_text(query.retrieval_text or query.query_text)
            )
            if query.version_scope == "multi_object":
                hits, retrieval_hits, candidate_groups = (
                    self._search_multi_object(query, vector)
                )
            else:
                retrieval_hits = tuple(self.store.search(query, vector))
                hits = self._trim_hits_by_score_decay(
                    retrieval_hits, query.query_depth
                )
                candidate_groups = ((query.business_object_id, hits),)
        except EvidenceRagError as exc:
            return _failed(exc.code, str(exc), permission, payload)
        except (KeyError, TypeError, ValueError) as exc:
            return _failed(
                "EVIDENCE_RAG_QUERY_INVALID", str(exc), permission, payload
            )
        if hits and query.version_scope == "single_object":
            profile_text = self._profile_text(payload)
            if profile_text:
                hits = (self._profile_hit(query, profile_text), *hits)
        if not hits:
            coverage = self._coverage(
                query,
                candidate_groups,
                selected_hits=hits,
                visible_hits=(),
            )
            if self.min_score > 0 and retrieval_hits:
                return _insufficient(
                    permission,
                    payload,
                    code="EVIDENCE_BELOW_THRESHOLD",
                    message=(
                        "retrieved Evidence is below the configured "
                        "retrieval threshold"
                    ),
                    coverage=coverage,
                )
            if self._index_building(payload):
                return _insufficient(
                    permission,
                    payload,
                    code="EVIDENCE_INDEX_NOT_READY",
                    message="当前图谱版本的检索索引尚未构建完成，请耐心等待",
                    coverage=coverage,
                )
            return _insufficient(permission, payload, coverage=coverage)
        try:
            visible_hits, evidence_context = self._evidence_context(
                hits,
                query.business_object_label,
                max_context_chars=self._context_budget(query),
                balanced_context=query.version_scope == "multi_object",
            )
            coverage = self._coverage(
                query,
                candidate_groups,
                selected_hits=hits,
                visible_hits=visible_hits,
            )
            if not visible_hits:
                return _insufficient(
                    permission,
                    payload,
                    code="EVIDENCE_CONTEXT_LIMIT",
                    message="no complete Evidence block fits the context limit",
                    coverage=coverage,
                )
            visible_ids = tuple(hit.evidence_id for hit in visible_hits)
            llm_kwargs: dict[str, object] = {
                "query_text": query.query_text,
                "evidence_text": evidence_context,
                "query_depth": query.query_depth,
            }
            if query.version_scope == "multi_object":
                visible_object_ids = tuple(
                    dict.fromkeys(
                        hit.business_object_id
                        for hit in visible_hits
                        if hit.business_object_id
                    )
                )
                llm_kwargs.update(
                    {
                        "business_object_ids": query.business_object_ids or (),
                        "objects_with_evidence": visible_object_ids,
                    }
                )
            entailments, answer = self.llm.judge_and_answer(
                **llm_kwargs,
            )
            entailments = self._validated_entailments(
                entailments, visible_ids
            )
        except EvidenceRagError as exc:
            return _failed(exc.code, str(exc), permission, payload)
        has_support = any(
            item.relation == "support" for item in entailments
        )
        has_contradict = any(
            item.relation == "contradict" for item in entailments
        )
        if has_contradict or not has_support:
            return _insufficient(
                permission,
                payload,
                code=(
                    "EVIDENCE_CONTRADICTED"
                    if has_contradict
                    else "EVIDENCE_INSUFFICIENT"
                ),
                message=answer.answer,
                entailment=entailments,
                coverage=coverage,
            )
        if not answer.supported:
            return _insufficient(
                permission,
                payload,
                code="EVIDENCE_INSUFFICIENT",
                message=answer.answer,
                entailment=entailments,
                coverage=coverage,
            )
        try:
            used_ids = self._used_evidence_ids(answer, visible_ids)
        except EvidenceRagError as exc:
            return _failed(exc.code, str(exc), permission, payload)
        if not used_ids:
            return _failed(
                "EVIDENCE_RAG_GROUNDING_INVALID",
                "supported RAG answer must declare used_evidence_ids",
                permission,
                payload,
            )
        try:
            references = tuple(_reference(hit) for hit in visible_hits)
            self._validate_reference_scope(query, references)
            return EvidenceRAGResponseV1(
                contract_version="evidence-rag-response.v1",
                status="answered",
                answer=answer.answer,
                supported=True,
                used_evidence_ids=list(used_ids),
                visible_evidence_ids=list(visible_ids),
                coverage=coverage,
                retrieval_threshold=self.min_score or None,
                entailment=[
                    _entailment_model(item) for item in entailments
                ],
                references=list(references),
                provider=self.llm.provider(),
                model=self.llm.model(),
                model_version=self.llm.model_version(),
                trace_id=get_trace_id(),
                permission=permission,
                **_response_version_kwargs(payload, scope=query.version_scope),
            )
        except EvidenceRagError as exc:
            return _failed(exc.code, str(exc), permission, payload)
        except ValidationError:
            logger.exception(
                "evidence_rag_response_contract_invalid "
                "error_code=EVIDENCE_RESPONSE_CONTRACT_INVALID trace_id=%s",
                get_trace_id(),
            )
            return _failed(
                "EVIDENCE_RESPONSE_CONTRACT_INVALID",
                "Evidence RAG response contract validation failed",
                permission,
                payload,
            )

    @staticmethod
    def _validate_reference_scope(
        query: EvidenceRagQuery,
        references: tuple[RAGEvidenceReferenceV1, ...],
    ) -> None:
        if query.version_scope == "multi_object":
            requested_versions = dict(query.business_object_versions or ())
            for reference in references:
                object_id = reference.business_object_id
                if object_id not in requested_versions:
                    raise EvidenceRagError(
                        "EVIDENCE_RESPONSE_VERSION_SCOPE_INVALID",
                        "Evidence reference business object is outside the query scope",
                    )
                if reference.graph_version_id != requested_versions[object_id]:
                    raise EvidenceRagError(
                        "EVIDENCE_RESPONSE_VERSION_SCOPE_INVALID",
                        "Evidence reference graph version does not match its business object",
                    )
            return

        for reference in references:
            if reference.business_object_id != query.business_object_id:
                raise EvidenceRagError(
                    "EVIDENCE_RESPONSE_VERSION_SCOPE_INVALID",
                    "Evidence reference business object does not match the query scope",
                )
            if (
                query.graph_version_id is not None
                and reference.graph_version_id != query.graph_version_id
            ):
                raise EvidenceRagError(
                    "EVIDENCE_RESPONSE_VERSION_SCOPE_INVALID",
                    "Evidence reference graph version does not match the query scope",
                )
            if (
                query.graph_version is not None
                and reference.graph_version != query.graph_version
            ):
                raise EvidenceRagError(
                    "EVIDENCE_RESPONSE_VERSION_SCOPE_INVALID",
                    "Evidence reference graph version does not match the query scope",
                )
            if (
                query.business_version is not None
                and reference.business_version != query.business_version
            ):
                raise EvidenceRagError(
                    "EVIDENCE_RESPONSE_VERSION_SCOPE_INVALID",
                    "Evidence reference business version does not match the query scope",
                )

    def _query(
        self, permission: PermissionContextV1, payload: Mapping[str, object]
    ) -> EvidenceRagQuery:
        business_object = payload.get("business_object")
        if not isinstance(business_object, Mapping):
            raise ValueError("business_object is required")
        raw_business_objects = payload.get("business_objects")
        raw_scope = payload.get("version_scope")
        if raw_scope is None:
            version_scope: RAGVersionScope = (
                "multi_object"
                if isinstance(raw_business_objects, (list, tuple))
                and len(raw_business_objects) > 1
                else "single_object"
            )
        elif raw_scope in ("single_object", "multi_object"):
            version_scope = raw_scope
        else:
            raise ValueError(
                "version_scope must be single_object or multi_object"
            )

        extra_object_ids: tuple[str, ...] = ()
        object_versions: tuple[tuple[str, int], ...] | None = None
        business_objects = raw_business_objects
        if business_objects is not None:
            if not isinstance(business_objects, (list, tuple)) or not business_objects:
                raise ValueError("business_objects must be a non-empty list")
            if any(not isinstance(item, Mapping) for item in business_objects):
                raise ValueError(
                    "business_objects must contain business object references"
                )
            if version_scope != "multi_object":
                raise ValueError(
                    "business_objects require version_scope=multi_object"
                )
            if len(
                {
                    str(item.get("object_type"))
                    for item in business_objects
                }
            ) != 1:
                raise ValueError(
                    "business_objects must use the same object type"
                )
            if str(business_objects[0].get("object_type")) != str(
                business_object.get("object_type")
            ):
                raise ValueError(
                    "business_objects must match the primary business_object type"
                )
            extra_object_ids = tuple(
                str(item.get("object_id")) for item in business_objects
            )
            if len(set(extra_object_ids)) != len(extra_object_ids):
                raise ValueError("business_objects must contain unique object ids")
            if str(business_object.get("object_id")) not in set(extra_object_ids):
                raise ValueError(
                    "business_objects must include the primary business_object"
                )
            declared_versions = tuple(
                item.get("object_version") for item in business_objects
            )
            if any(version is None for version in declared_versions):
                raise ValueError(
                    "multi_object RAG query requires each business object version"
                )
            if str(business_object.get("object_type")) != "standard_position":
                raise ValueError(
                    "multi-object RAG currently requires standard_position graph versions"
                )
            try:
                object_versions = tuple(
                    (str(item["object_id"]), int(str(item["object_version"])))
                    for item in business_objects
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "business object graph versions must be positive integers"
                ) from exc
        elif version_scope == "multi_object":
            raise ValueError(
                "multi_object RAG query requires business_objects"
            )

        global_version_identity = (
            payload.get("graph_version_id"),
            payload.get("graph_version"),
            payload.get("business_version"),
        )
        if version_scope == "multi_object":
            if any(identity is not None for identity in global_version_identity):
                raise ValueError(
                    "multi_object RAG query must not declare a global version identity"
                )
        elif business_objects is not None:
            raise ValueError(
                "business_objects require version_scope=multi_object"
            )

        object_ids = tuple(
            dict.fromkeys(
                (str(business_object["object_id"]), *extra_object_ids)
            )
        )
        evidence_types = payload.get("evidence_types")
        if not isinstance(evidence_types, (list, tuple)) or not evidence_types:
            raise ValueError("evidence_types must be a non-empty list")
        current_query_text = str(payload["query_text"])
        query_depth = self._classify_query_depth(current_query_text)
        history = payload.get("conversation_history") or ()
        if not isinstance(history, (list, tuple)):
            raise ValueError("conversation_history must be a list")
        history_lines = tuple(
            f"{'用户' if str(turn.get('role')) == 'user' else '助手'}：{str(turn.get('text'))}"
            for turn in history
            if isinstance(turn, Mapping)
        )
        contextual_query_text = current_query_text
        if history_lines:
            contextual_query_text = "\n".join(
                ("对话历史：", *history_lines, f"用户当前问题：{current_query_text}")
            )
        return EvidenceRagQuery(
            query_text=contextual_query_text,
            retrieval_text=current_query_text,
            business_object_type=str(business_object["object_type"]),
            business_object_id=str(business_object["object_id"]),
            version_scope=version_scope,
            business_object_ids=(
                object_ids if len(object_ids) > 1 else None
            ),
            business_object_versions=object_versions,
            evidence_types=tuple(str(item) for item in evidence_types),
            tenant_ref=permission.tenant_ref,
            permission_scope=permission.permission_scope,
            business_object_label=payload.get("business_object_label"),
            graph_version_id=payload.get("graph_version_id"),
            graph_version=payload.get("graph_version"),
            business_version=payload.get("business_version"),
            top_k=self._query_top_k(query_depth),
            query_depth=query_depth,
        )

    def _index_building(self, payload: Mapping[str, object]) -> bool:
        if self.index_status_provider is None:
            return False
        try:
            status = self.index_status_provider(payload)
        except Exception:
            return False
        return (
            isinstance(status, Mapping)
            and status.get("status") == "running"
        )

    def _profile_text(
        self, payload: Mapping[str, object]
    ) -> str | None:
        if self.profile_text_provider is None:
            return None
        try:
            text = self.profile_text_provider(payload)
        except Exception:
            return None
        if not isinstance(text, str) or not text.strip():
            return None
        return text.strip()

    @staticmethod
    def _retrieval_text(query_text: str) -> str:
        qualification_hints = (
            "适合",
            "参加",
            "能不能",
            "可以吗",
            "是否可以",
            "学历",
            "经验",
            "高中生",
            "小学生",
            "大学生",
            "应届",
            "要求",
            "需要",
            "资格",
            "门槛",
            "符合",
            "够格",
        )
        if any(hint in query_text for hint in qualification_hints):
            return f"{query_text}。这个岗位要求什么学历？"
        return query_text

    @staticmethod
    def _classify_query_depth(query_text: str) -> EvidenceQueryDepth:
        normalized = query_text.strip()
        if any(hint in normalized for hint in QUERY_DEPTH_COMPARE_HINTS):
            return "compare"
        if any(hint in normalized for hint in QUERY_DEPTH_OVERVIEW_HINTS):
            return "overview"
        return "fact"

    def _query_top_k(self, query_depth: EvidenceQueryDepth) -> int:
        desired = QUERY_DEPTH_TOP_K.get(query_depth, QUERY_DEPTH_TOP_K["fact"])
        return min(QUERY_DEPTH_MAX_K, max(desired, self.top_k))

    def _search_multi_object(
        self,
        query: EvidenceRagQuery,
        vector: list[float],
    ) -> tuple[
        tuple[EvidenceRagHit, ...],
        tuple[EvidenceRagHit, ...],
        tuple[tuple[str, tuple[EvidenceRagHit, ...]], ...],
    ]:
        requested_versions = dict(query.business_object_versions or ())
        per_object_k = MULTI_OBJECT_PER_OBJECT_K.get(
            query.query_depth, MULTI_OBJECT_PER_OBJECT_K["fact"]
        )
        groups: list[tuple[str, tuple[EvidenceRagHit, ...]]] = []
        retrieval_hits: list[EvidenceRagHit] = []
        for object_id in query.business_object_ids or ():
            object_query = replace(
                query,
                business_object_id=object_id,
                business_object_ids=None,
                business_object_versions=None,
                version_scope="single_object",
                graph_version_id=requested_versions[object_id],
                graph_version=None,
                business_version=None,
                top_k=per_object_k,
            )
            object_hits = tuple(self.store.search(object_query, vector))
            retrieval_hits.extend(object_hits)
            groups.append(
                (
                    object_id,
                    self._trim_hits_by_score_decay(
                        object_hits, query.query_depth
                    ),
                )
            )
        return (
            self._balanced_merge(tuple(groups), self.multi_object_max_hits),
            tuple(retrieval_hits),
            tuple(groups),
        )

    @staticmethod
    def _balanced_merge(
        groups: tuple[tuple[str, tuple[EvidenceRagHit, ...]], ...],
        max_hits: int,
    ) -> tuple[EvidenceRagHit, ...]:
        merged: list[EvidenceRagHit] = []
        max_group_size = max(
            (len(group_hits) for _, group_hits in groups),
            default=0,
        )
        for round_index in range(max_group_size):
            for _, group_hits in groups:
                if round_index >= len(group_hits):
                    continue
                merged.append(group_hits[round_index])
                if len(merged) >= max_hits:
                    return tuple(merged)
        return tuple(merged)

    def _context_budget(self, query: EvidenceRagQuery) -> int:
        if query.version_scope != "multi_object":
            return self.max_context_chars
        object_count = len(query.business_object_ids or ())
        if object_count <= 1:
            return self.max_context_chars
        budget = MULTI_OBJECT_CONTEXT_BUDGETS[-1][1]
        for maximum_count, candidate_budget in MULTI_OBJECT_CONTEXT_BUDGETS:
            if object_count <= maximum_count:
                budget = candidate_budget
                break
        return min(budget, self.multi_object_max_context_chars)

    @staticmethod
    def _coverage(
        query: EvidenceRagQuery,
        candidate_groups: tuple[
            tuple[str, tuple[EvidenceRagHit, ...]], ...
        ],
        *,
        selected_hits: tuple[EvidenceRagHit, ...],
        visible_hits: tuple[EvidenceRagHit, ...],
    ) -> EvidenceRAGCoverageV1:
        object_ids = query.business_object_ids or (query.business_object_id,)
        candidate_object_ids = {
            object_id for object_id, group_hits in candidate_groups if group_hits
        }
        evidence_count_by_object = {object_id: 0 for object_id in object_ids}
        for hit in selected_hits:
            if hit.business_object_id in evidence_count_by_object:
                evidence_count_by_object[hit.business_object_id] += 1
        visible_object_ids = {
            hit.business_object_id
            for hit in visible_hits
            if hit.business_object_id in evidence_count_by_object
        }
        return EvidenceRAGCoverageV1(
            selected_object_count=len(object_ids),
            objects_with_candidates=len(candidate_object_ids),
            objects_with_visible_evidence=len(visible_object_ids),
            evidence_count_by_object=evidence_count_by_object,
        )

    def _trim_hits_by_score_decay(
        self,
        hits: tuple[EvidenceRagHit, ...],
        query_depth: EvidenceQueryDepth,
    ) -> tuple[EvidenceRagHit, ...]:
        if not hits:
            return ()
        ordered = tuple(
            sorted(hits, key=lambda hit: (-hit.score, hit.evidence_id))
        )
        # Hard gate: min_score is applied before any retained-count logic.
        # Below-threshold Evidence is never re-added, even when fewer than three
        # results survive, and filtering is skipped entirely when min_score is 0.
        if self.min_score > 0:
            ordered = tuple(
                hit for hit in ordered if hit.score >= self.min_score
            )
        if not ordered:
            return ()
        if len(ordered) <= QUERY_DEPTH_MIN_RETAINED:
            return ordered
        best_score = ordered[0].score
        decay_ratio = QUERY_DEPTH_SCORE_DECAY_RATIO.get(
            query_depth, QUERY_DEPTH_SCORE_DECAY_RATIO["fact"]
        )
        score_drop = QUERY_DEPTH_SCORE_DROP.get(
            query_depth, QUERY_DEPTH_SCORE_DROP["fact"]
        )
        retained = list(ordered[:QUERY_DEPTH_MIN_RETAINED])
        for current in ordered[QUERY_DEPTH_MIN_RETAINED:]:
            previous_score = retained[-1].score
            if (
                current.score < best_score * decay_ratio
                or previous_score - current.score > score_drop
            ):
                break
            retained.append(current)
        return tuple(retained)

    def _profile_hit(
        self,
        query: EvidenceRagQuery,
        profile_text: str,
    ) -> EvidenceRagHit:
        graph_version_id = query.graph_version_id or 0
        return EvidenceRagHit(
            evidence_id=f"position-profile:{graph_version_id}",
            source_object_type="position_profile",
            source_object_id=query.business_object_id,
            source_document_id=f"graph-version:{graph_version_id}",
            source_version="graph-snapshot",
            score=1.0,
            quote=profile_text,
            location_start=None,
            location_end=None,
            occurrence_index=None,
            alignment="unresolved",
            graph_version_id=query.graph_version_id,
            graph_version=query.graph_version,
            business_version=query.business_version,
            tenant_ref=query.tenant_ref,
            permission_scope=query.permission_scope,
            business_object_id=query.business_object_id,
            business_object_name=query.business_object_label,
        )

    def _evidence_context(
        self,
        hits: tuple[EvidenceRagHit, ...],
        business_object_label: str | None = None,
        *,
        max_context_chars: int | None = None,
        balanced_context: bool = False,
    ) -> tuple[tuple[EvidenceRagHit, ...], str]:
        context_limit = (
            self.max_context_chars
            if max_context_chars is None
            else max_context_chars
        )
        prefix = (
            f"业务对象：{business_object_label}\n"
            if business_object_label
            else ""
        )
        blocks: list[str] = []
        for index, hit in enumerate(hits, start=1):
            quote = hit.quote or hit.source_document_id
            if hit.evidence_id.startswith("position-profile:"):
                blocks.append(
                    f"[{index}] 岗位画像 evidence_id={hit.evidence_id}：{quote}"
                )
            else:
                blocks.append(
                    f"[{index}] "
                    + (
                        f"岗位={hit.business_object_name} "
                        if hit.business_object_name
                        else ""
                    )
                    + (
                        f"对象={hit.business_object_id} "
                        if balanced_context and hit.business_object_id
                        else ""
                    )
                    + f"evidence_id={hit.evidence_id} "
                    f"source={hit.source_object_type}:{hit.source_object_id} "
                    f"version={hit.source_version} quote={quote}"
                )
        visible: list[EvidenceRagHit] = []
        selected: list[str] = []
        for index, hit in enumerate(hits):
            block = blocks[index]
            candidate = [*selected, block]
            text = prefix + "\n".join(candidate)
            if len(text) + len(candidate) - 1 > context_limit:
                if balanced_context:
                    continue
                break
            visible.append(hit)
            selected.append(block)
        return tuple(visible), prefix + "\n".join(selected)

    @staticmethod
    def _used_evidence_ids(
        answer: EvidenceRagLlmAnswer,
        visible_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        visible = set(visible_ids)
        unknown = sorted(
            evidence_id
            for evidence_id in answer.used_evidence_ids
            if evidence_id not in visible
        )
        if unknown:
            raise EvidenceRagError(
                "EVIDENCE_RAG_USED_EVIDENCE_INVALID",
                "LLM returned used evidence ids outside the visible Evidence: "
                + ", ".join(unknown),
            )
        return tuple(
            dict.fromkeys(
                evidence_id
                for evidence_id in answer.used_evidence_ids
                if evidence_id in visible
            )
        )

    @staticmethod
    def _validated_entailments(
        entailments: tuple[EvidenceEntailment, ...],
        visible_ids: tuple[str, ...],
    ) -> tuple[EvidenceEntailment, ...]:
        visible = set(visible_ids)
        validated: list[EvidenceEntailment] = []
        for item in entailments:
            unknown = sorted(
                evidence_id
                for evidence_id in item.used_evidence_ids
                if evidence_id not in visible
            )
            if unknown:
                raise EvidenceRagError(
                    "EVIDENCE_RAG_ENTAILMENT_INVALID",
                    "entailment references evidence outside the visible set: "
                    + ", ".join(unknown),
                )
            validated.append(item)
        return tuple(validated)

    def _require_internal(self, actor: AccountActor) -> None:
        if actor.role not in INTERNAL_RAG_ROLES:
            raise PermissionDenied("Evidence RAG management requires admin or developer")

    @staticmethod
    def _optional(values: Mapping[str, object], key: str) -> str | None:
        value = values.get(key)
        if value is None:
            return None
        return str(value)


__all__ = [
    "ENTERPRISE_SCOPE_PREFIX",
    "INTERNAL_RAG_ROLES",
    "ManageEvidenceRag",
    "PERSONAL_SCOPE_PREFIX",
    "PLATFORM_PERMISSION_SCOPE",
    "RAG_INDEX_BATCH_SIZE",
]
