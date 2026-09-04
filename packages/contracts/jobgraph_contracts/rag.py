"""Minimal Evidence RAG query/response contract.

This contract only defines the wire format and invariants. It does not
implement indexing, retrieval, or generation. RAG must refuse to answer
without valid Evidence and must never cross tenant or GraphVersion boundaries.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from jobgraph_contracts.base import StrictContract


EVIDENCE_RAG_QUERY_VERSION = "evidence-rag-query.v1"
EVIDENCE_RAG_RESPONSE_VERSION = "evidence-rag-response.v1"
PLATFORM_PUBLIC_TENANT_REF = "jobgraph-platform-public"
PLATFORM_PERMISSION_SCOPE = "platform:public"

EvidenceTypeScope = Literal[
    "jd_evidence",
    "cv_evidence",
    "kg_skill_relation_evidence",
    "trend_evidence",
    "discovery_evidence",
    "matching_evidence",
    "gap_evidence",
    "learning_path_evidence",
    "review_decision_evidence",
    "all",
]

RAGStatus = Literal["answered", "insufficient_evidence", "failed"]
EvidenceAlignment = Literal["exact", "normalized_exact", "unresolved"]
EvidenceEntailmentRelation = Literal["support", "contradict", "insufficient"]
RAGVersionScope = Literal["single_object", "multi_object"]


def _version_identity(
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


def _reference_version_identity(
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


class BusinessObjectRef(StrictContract):
    object_type: Literal[
        "source_jd",
        "source_cv",
        "standard_position",
        "enterprise_job",
        "cv_profile",
        "matching_evaluation",
        "trend_report",
        "discovery_cluster",
        "graph_version",
        "resume",
    ]
    object_id: str = Field(min_length=1, max_length=160)
    object_version: str | None = Field(default=None, min_length=1, max_length=160)


def _validate_business_object_scope(
    *,
    version_scope: RAGVersionScope,
    business_object: BusinessObjectRef,
    business_objects: list[BusinessObjectRef] | None,
) -> None:
    if version_scope == "single_object":
        if business_objects is not None:
            raise ValueError(
                "single_object RAG query must not include business_objects"
            )
        return

    if business_objects is None or len(business_objects) < 2:
        raise ValueError(
            "multi_object RAG query requires at least two business_objects"
        )
    if any(
        item.object_type != business_object.object_type
        for item in business_objects
    ):
        raise ValueError("business_objects must use the same object type")
    object_ids = [item.object_id for item in business_objects]
    if len(object_ids) != len(set(object_ids)):
        raise ValueError("business_objects must use unique object ids")
    if business_object.object_id not in object_ids:
        raise ValueError("business_objects must include the primary business_object")
    if any(item.object_version is None for item in business_objects):
        raise ValueError(
            "multi_object RAG query requires an object_version for every business_object"
        )


class PermissionContextV1(StrictContract):
    user_id: str = Field(
        min_length=1,
        max_length=160,
        description="execution subject and audit identity; not evidence owner",
    )
    tenant_ref: str = Field(min_length=1, max_length=160)
    permission_scope: str = Field(min_length=1, max_length=200)
    assembled_by: Literal["main-system-bff"] = "main-system-bff"


class EvidenceRAGQueryV1(StrictContract):
    contract_version: Literal["evidence-rag-query.v1"] = "evidence-rag-query.v1"
    business_object: BusinessObjectRef
    business_object_label: str | None = Field(
        default=None, min_length=1, max_length=160
    )
    business_objects: list[BusinessObjectRef] | None = Field(
        default=None, max_length=200
    )
    query_text: str = Field(min_length=1)
    evidence_types: list[EvidenceTypeScope] = Field(min_length=1)
    version_scope: RAGVersionScope = "single_object"
    graph_version_id: int | None = Field(default=None, ge=1)
    graph_version: str | None = Field(default=None, min_length=1, max_length=80)
    business_version: str | None = Field(default=None, min_length=1, max_length=160)
    permission: PermissionContextV1

    @model_validator(mode="after")
    def validate_scope(self) -> "EvidenceRAGQueryV1":
        _validate_business_object_scope(
            version_scope=self.version_scope,
            business_object=self.business_object,
            business_objects=self.business_objects,
        )
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
        else:
            _version_identity(*identities)
        if "all" in self.evidence_types and len(self.evidence_types) != 1:
            raise ValueError("evidence type all must be used alone")
        return self


class RAGEvidenceReferenceV1(StrictContract):
    evidence_id: str = Field(min_length=1)
    business_object_id: str | None = Field(default=None, min_length=1, max_length=160)
    source_object_type: str = Field(min_length=1)
    source_object_id: str = Field(min_length=1)
    source_document_id: str = Field(min_length=1)
    retrieval_score: float | None = Field(default=None, ge=-1, le=1)
    quote: str | None = Field(default=None, min_length=1)
    location_start: int | None = Field(default=None, ge=0)
    location_end: int | None = Field(default=None, ge=0)
    occurrence_index: int | None = Field(default=None, ge=0)
    alignment: EvidenceAlignment = "unresolved"
    graph_version_id: int | None = Field(default=None, ge=1)
    graph_version: str | None = Field(default=None, min_length=1, max_length=80)
    business_version: str | None = Field(default=None, min_length=1, max_length=160)
    source_version: str = Field(min_length=1)
    tenant_ref: str = Field(min_length=1)
    permission_scope: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_reference(self) -> "RAGEvidenceReferenceV1":
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
            raise ValueError("evidence reference requires quote or location span")
        if not self.quote and self.alignment == "exact":
            raise ValueError("exact evidence reference requires quote")
        _reference_version_identity(
            self.graph_version_id, self.graph_version, self.business_version
        )
        return self


class RAGErrorV1(StrictContract):
    code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=500)


class EvidenceEntailmentV1(StrictContract):
    claim: str = Field(min_length=1, max_length=500)
    relation: EvidenceEntailmentRelation
    used_evidence_ids: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=1000)
    dimension: str | None = Field(default=None, min_length=1, max_length=80)


class EvidenceRAGCoverageV1(StrictContract):
    selected_object_count: int = Field(ge=1)
    objects_with_candidates: int = Field(ge=0)
    objects_with_visible_evidence: int = Field(ge=0)
    evidence_count_by_object: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_counts(self) -> "EvidenceRAGCoverageV1":
        if self.objects_with_candidates > self.selected_object_count:
            raise ValueError("RAG coverage candidates exceed selected objects")
        if self.objects_with_visible_evidence > self.objects_with_candidates:
            raise ValueError(
                "RAG coverage visible objects exceed candidate objects"
            )
        if any(
            not object_id.strip() or count < 0
            for object_id, count in self.evidence_count_by_object.items()
        ):
            raise ValueError("RAG coverage evidence counts are invalid")
        return self


class EvidenceRAGResponseV1(StrictContract):
    contract_version: Literal["evidence-rag-response.v1"] = "evidence-rag-response.v1"
    status: RAGStatus
    answer: str | None = Field(default=None, min_length=1)
    references: list[RAGEvidenceReferenceV1] = Field(default_factory=list)
    entailment: list[EvidenceEntailmentV1] = Field(default_factory=list)
    supported: bool | None = None
    used_evidence_ids: list[str] = Field(default_factory=list)
    visible_evidence_ids: list[str] = Field(default_factory=list)
    coverage: EvidenceRAGCoverageV1 | None = None
    retrieval_threshold: float | None = Field(default=None, ge=-1, le=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    error: RAGErrorV1 | None = None
    explanation_only: Literal[True] = True
    version_scope: RAGVersionScope = "single_object"
    graph_version_id: int | None = Field(default=None, ge=1)
    graph_version: str | None = Field(default=None, min_length=1, max_length=80)
    business_version: str | None = Field(default=None, min_length=1, max_length=160)
    permission: PermissionContextV1

    @model_validator(mode="after")
    def validate_status(self) -> "EvidenceRAGResponseV1":
        identities = (
            self.graph_version_id,
            self.graph_version,
            self.business_version,
        )
        if self.version_scope == "multi_object":
            if any(identity is not None for identity in identities):
                raise ValueError(
                    "multi_object RAG response must not declare a global version identity"
                )
        else:
            _version_identity(*identities)
        if self.status == "answered":
            if not self.answer or not self.references:
                raise ValueError(
                    "answered RAG requires an answer and at least one evidence reference"
                )
            if self.supported is False:
                raise ValueError("answered RAG cannot mark evidence unsupported")
            visible_ids = {
                reference.evidence_id for reference in self.references
            } | set(self.visible_evidence_ids)
            unknown_used = sorted(
                evidence_id
                for evidence_id in self.used_evidence_ids
                if evidence_id not in visible_ids
            )
            if unknown_used:
                raise ValueError(
                    "used evidence ids must reference visible evidence: "
                    + ", ".join(unknown_used)
                )
            if self.supported is True and not self.used_evidence_ids:
                raise ValueError("supported RAG requires used evidence ids")
            if self.error is not None:
                raise ValueError("answered RAG cannot carry an error")
            if self.version_scope == "multi_object" and any(
                reference.business_object_id is None
                for reference in self.references
            ):
                raise ValueError(
                    "multi_object RAG references require business_object_id"
                )
        elif self.status == "insufficient_evidence":
            if self.answer is not None or self.references:
                raise ValueError(
                    "insufficient_evidence RAG cannot fabricate an answer or references"
                )
            if self.supported is not None or self.used_evidence_ids:
                raise ValueError(
                    "insufficient_evidence RAG cannot claim used evidence"
                )
            if self.error is None or self.error.code not in {
                "EVIDENCE_INSUFFICIENT",
                "EVIDENCE_NOT_FOUND",
                "EVIDENCE_BELOW_THRESHOLD",
                "EVIDENCE_CONTEXT_LIMIT",
                "EVIDENCE_INDEX_NOT_READY",
                "EVIDENCE_CONTRADICTED",
            }:
                raise ValueError(
                    "insufficient_evidence RAG requires an evidence error code"
                )
        else:
            if self.answer is not None or self.references:
                raise ValueError("failed RAG cannot fabricate an answer or references")
            if self.supported is not None or self.used_evidence_ids:
                raise ValueError("failed RAG cannot claim used evidence")
            if self.error is None:
                raise ValueError("failed RAG requires an error")

        for reference in self.references:
            own_scope = (
                reference.tenant_ref == self.permission.tenant_ref
                and reference.permission_scope
                == self.permission.permission_scope
            )
            public_scope = (
                reference.tenant_ref == PLATFORM_PUBLIC_TENANT_REF
                and reference.permission_scope == PLATFORM_PERMISSION_SCOPE
            )
            if not own_scope and not public_scope:
                if reference.tenant_ref != self.permission.tenant_ref:
                    raise ValueError("RAG evidence cannot cross tenant boundaries")
                raise ValueError("RAG evidence cannot cross permission scopes")
            if (
                self.graph_version is not None
                and reference.graph_version != self.graph_version
            ):
                raise ValueError("RAG evidence cannot cross graph versions")
            if (
                self.graph_version_id is not None
                and reference.graph_version_id != self.graph_version_id
            ):
                raise ValueError("RAG evidence cannot cross graph versions")
            if (
                self.business_version is not None
                and reference.business_version != self.business_version
            ):
                raise ValueError("RAG evidence cannot cross business versions")
        return self


__all__ = [
    "BusinessObjectRef",
    "EVIDENCE_RAG_QUERY_VERSION",
    "EVIDENCE_RAG_RESPONSE_VERSION",
    "EvidenceAlignment",
    "EvidenceRAGQueryV1",
    "EvidenceRAGResponseV1",
    "EvidenceRAGCoverageV1",
    "RAGVersionScope",
    "EvidenceTypeScope",
    "PermissionContextV1",
    "PLATFORM_PERMISSION_SCOPE",
    "PLATFORM_PUBLIC_TENANT_REF",
    "RAGErrorV1",
    "RAGEvidenceReferenceV1",
    "RAGStatus",
]
