from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jobgraph_contracts.rag import (
    BusinessObjectRef,
    EvidenceTypeScope,
    PermissionContextV1,
    RAGVersionScope,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceRagConversationTurn(_StrictModel):
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=4000)


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


class EvidenceRagBFFRequest(_StrictModel):
    contract_version: Literal["evidence-rag-query.v1"] = "evidence-rag-query.v1"
    business_object: BusinessObjectRef
    business_object_label: str | None = Field(
        default=None, min_length=1, max_length=160
    )
    business_objects: list[BusinessObjectRef] | None = Field(
        default=None, max_length=200
    )
    query_text: str = Field(min_length=1, max_length=4000)
    conversation_history: list[EvidenceRagConversationTurn] = Field(
        default_factory=list, max_length=12
    )
    evidence_types: list[EvidenceTypeScope] = Field(min_length=1)
    version_scope: RAGVersionScope = "single_object"
    graph_version_id: int | None = Field(default=None, ge=1)
    graph_version: str | None = Field(default=None, min_length=1, max_length=80)
    business_version: str | None = Field(default=None, min_length=1, max_length=160)
    permission: PermissionContextV1 | None = None

    @model_validator(mode="after")
    def validate_version(self) -> "EvidenceRagBFFRequest":
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
            _require_version_identity(*identities)
        return self

    @model_validator(mode="after")
    def validate_scope(self) -> "EvidenceRagBFFRequest":
        if "all" in self.evidence_types and len(self.evidence_types) != 1:
            raise ValueError("evidence type all must be used alone")
        if self.version_scope == "single_object":
            if self.business_objects is not None:
                raise ValueError(
                    "single_object RAG query must not include business_objects"
                )
            return self
        if self.business_objects is None or len(self.business_objects) < 2:
            raise ValueError(
                "multi_object RAG query requires at least two business_objects"
            )
        if any(
            item.object_type != self.business_object.object_type
            for item in self.business_objects
        ):
            raise ValueError("business_objects must use the same object type")
        object_ids = [item.object_id for item in self.business_objects]
        if len(object_ids) != len(set(object_ids)):
            raise ValueError("business_objects must use unique object ids")
        if self.business_object.object_id not in object_ids:
            raise ValueError(
                "business_objects must include the primary business_object"
            )
        if any(item.object_version is None for item in self.business_objects):
            raise ValueError(
                "multi_object RAG query requires an object_version for every business_object"
            )
        return self


class EvidenceRagIndexItem(_StrictModel):
    evidence_id: str = Field(min_length=1, max_length=160)
    business_object_type: str = Field(min_length=1, max_length=80)
    business_object_id: str = Field(min_length=1, max_length=160)
    business_object_name: str | None = Field(
        default=None, min_length=1, max_length=160
    )
    evidence_type: EvidenceTypeScope
    source_object_type: str = Field(min_length=1, max_length=80)
    source_object_id: str = Field(min_length=1, max_length=160)
    source_document_id: str = Field(min_length=1, max_length=160)
    source_version: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=12000)
    tenant_ref: str = Field(min_length=1, max_length=160)
    permission_scope: str = Field(min_length=1, max_length=200)
    quote: str | None = Field(default=None, min_length=1)
    location_start: int | None = Field(default=None, ge=0)
    location_end: int | None = Field(default=None, ge=0)
    occurrence_index: int | None = Field(default=None, ge=0)
    alignment: Literal["exact", "normalized_exact", "unresolved"] = "unresolved"
    graph_version_id: int | None = Field(default=None, ge=1)
    graph_version: str | None = Field(default=None, min_length=1, max_length=80)
    business_version: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_version(self) -> "EvidenceRagIndexItem":
        if self.evidence_type == "all":
            raise ValueError("indexed Evidence must use a concrete evidence type")
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
            raise ValueError("Evidence record requires quote or location span")
        if not self.quote and self.alignment == "exact":
            raise ValueError("exact Evidence record requires quote")
        return self


class EvidenceRagIndexBody(_StrictModel):
    items: list[EvidenceRagIndexItem] = Field(min_length=1, max_length=1000)


class EvidenceRagScopeFilter(_StrictModel):
    tenant_ref: str = Field(min_length=1, max_length=160)
    permission_scope: str = Field(min_length=1, max_length=200)
    source_object_type: str | None = Field(default=None, min_length=1, max_length=80)
    source_object_id: str | None = Field(default=None, min_length=1, max_length=160)
    source_document_id: str | None = Field(default=None, min_length=1, max_length=160)
    source_version: str | None = Field(default=None, min_length=1, max_length=160)


class EvidenceCitationResolveRequest(_StrictModel):
    evidence_id: str = Field(min_length=1, max_length=160)
    source_version: str = Field(min_length=1, max_length=160)
    graph_version_id: int | None = Field(default=None, ge=1)
    graph_version: str | None = Field(default=None, min_length=1, max_length=80)
    business_version: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_version(self) -> "EvidenceCitationResolveRequest":
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
        return self


class EvidenceCitationResolution(_StrictModel):
    contract_version: Literal["evidence-citation-resolution.v1"]
    target_route: str = Field(min_length=1, max_length=2048)
    resource_id: str = Field(min_length=1, max_length=160)
    version_id: str | int
    evidence_id: str = Field(min_length=1, max_length=160)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)
    highlight_text: str
    source_object_type: str = Field(min_length=1, max_length=80)
    source_object_id: str = Field(min_length=1, max_length=160)
    source_document_id: str = Field(min_length=1, max_length=160)
    source_version: str = Field(min_length=1, max_length=160)
    graph_version_id: int | None = Field(default=None, ge=1)
    graph_version: str | None = None
    business_version: str | None = None

    @model_validator(mode="after")
    def validate_span(self) -> "EvidenceCitationResolution":
        if (self.start is None) != (self.end is None):
            raise ValueError("citation start and end must be supplied together")
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("citation end must not precede start")
        return self


__all__ = [
    "EvidenceRagBFFRequest",
    "EvidenceCitationResolveRequest",
    "EvidenceCitationResolution",
    "EvidenceRagIndexBody",
    "EvidenceRagIndexItem",
    "EvidenceRagScopeFilter",
]
