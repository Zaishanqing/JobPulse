from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import httpx

from app.contexts.evidence_rag.contracts import (
    EvidenceCitationQuery,
    EvidenceRagError,
    EvidenceRagHit,
    EvidenceRagQuery,
    EvidenceRagRecord,
)
from app.contexts.evidence_rag.application import PLATFORM_PERMISSION_SCOPE
from app.profile_index_events import PLATFORM_PUBLIC_TENANT_REF


_KEYWORD_INDEXES = (
    "tenant_ref",
    "permission_scope",
    "business_object_type",
    "business_object_id",
    "evidence_type",
    "evidence_id",
    "source_object_type",
    "source_object_id",
    "source_document_id",
    "source_version",
    "graph_version",
    "business_version",
    "graph_version_id",
)
_BOOL_INDEXES = ("active",)


def _point_identity(
    *,
    evidence_id: object,
    source_version: object,
    graph_version_id: object,
    graph_version: object,
    business_version: object,
) -> tuple[str, str, str, str, str]:
    return (
        str(evidence_id or ""),
        str(source_version or ""),
        str(graph_version_id or ""),
        str(graph_version or ""),
        str(business_version or ""),
    )


class QdrantEvidenceRagStore:
    def __init__(
        self,
        url: str,
        *,
        collection_name: str,
        dimension: int,
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.1,
        client: httpx.Client | None = None,
    ) -> None:
        if not url.strip() or not collection_name.strip():
            raise ValueError("Qdrant URL and collection name are required")
        if dimension <= 0 or timeout_seconds <= 0:
            raise ValueError("Qdrant dimension and timeout must be positive")
        if max_retries < 0 or retry_backoff_seconds < 0:
            raise ValueError("Qdrant retry configuration cannot be negative")
        self._client = client or httpx.Client(
            base_url=url.rstrip("/"),
            timeout=timeout_seconds,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        self.collection_name = collection_name
        self.dimension = dimension
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff_seconds
        self._initialized = False

    def initialize(self) -> None:
        path = f"/collections/{self.collection_name}"
        response = self._request("GET", path, accepted_statuses={200, 404})
        existing_indexes: set[str] = set()
        if response.status_code == 404:
            self._request(
                "PUT",
                path,
                json={
                    "vectors": {"size": self.dimension, "distance": "Cosine"},
                    "on_disk_payload": True,
                },
            )
        else:
            body = self._json(response)
            self._validate_vector_schema(body)
            result = body.get("result")
            if isinstance(result, Mapping):
                payload_schema = result.get("payload_schema")
                if isinstance(payload_schema, Mapping):
                    existing_indexes = set(payload_schema)
        for field_name in _KEYWORD_INDEXES:
            if field_name in existing_indexes:
                continue
            self._request(
                "PUT",
                f"{path}/index",
                params={"wait": "true"},
                json={"field_name": field_name, "field_schema": "keyword"},
            )
        for field_name in _BOOL_INDEXES:
            if field_name in existing_indexes:
                continue
            self._request(
                "PUT",
                f"{path}/index",
                params={"wait": "true"},
                json={"field_name": field_name, "field_schema": "bool"},
            )
        self._initialized = True

    def upsert(self, record: EvidenceRagRecord, vector: list[float]) -> None:
        self.upsert_many((record,), (tuple(vector),))

    def upsert_many(
        self,
        records: tuple[EvidenceRagRecord, ...],
        vectors: tuple[tuple[float, ...], ...],
    ) -> None:
        self._require_initialized()
        if not records:
            return
        if len(records) != len(vectors):
            raise EvidenceRagError(
                "QDRANT_BATCH_MISMATCH",
                "Evidence record and vector counts must match",
            )
        existing_ids = self._find_point_ids(records)
        points: list[dict[str, object]] = []
        for record, vector in zip(records, vectors):
            if len(vector) != self.dimension:
                raise EvidenceRagError(
                    "QDRANT_DIMENSION_MISMATCH",
                    "Evidence vector dimension does not match the collection",
                )
            points.append(
                {
                    "id": existing_ids.get(
                        _point_identity(
                            evidence_id=record.evidence_id,
                            source_version=record.source_version,
                            graph_version_id=record.graph_version_id,
                            graph_version=record.graph_version,
                            business_version=record.business_version,
                        )
                    )
                    or str(uuid4()),
                    "vector": list(vector),
                    "payload": self._payload(record),
                }
            )
        self._request(
            "PUT",
            f"/collections/{self.collection_name}/points",
            params={"wait": "true"},
            json={"points": points},
        )

    def search(
        self, query: EvidenceRagQuery, vector: list[float]
    ) -> tuple[EvidenceRagHit, ...]:
        self._require_initialized()
        if len(vector) != self.dimension:
            raise EvidenceRagError(
                "QDRANT_DIMENSION_MISMATCH",
                "query vector dimension does not match the collection",
            )
        conditions = [
            {
                "should": [
                    {
                        "must": [
                            {"key": "tenant_ref", "match": {"value": query.tenant_ref}},
                            {
                                "key": "permission_scope",
                                "match": {"value": query.permission_scope},
                            },
                        ]
                    },
                    {
                        "must": [
                            {
                                "key": "tenant_ref",
                                "match": {"value": PLATFORM_PUBLIC_TENANT_REF},
                            },
                            {
                                "key": "permission_scope",
                                "match": {"value": PLATFORM_PERMISSION_SCOPE},
                            },
                        ]
                    },
                ]
            },
            {"key": "active", "match": {"value": True}},
            {
                "key": "business_object_type",
                "match": {"value": query.business_object_type},
            },
        ]
        if query.business_object_versions is not None:
            conditions.append(
                {
                    "should": [
                        {
                            "must": [
                                {
                                    "key": "business_object_id",
                                    "match": {"value": object_id},
                                },
                                {
                                    "key": "graph_version_id",
                                    "match": {"value": str(version_id)},
                                },
                            ]
                        }
                        for object_id, version_id in query.business_object_versions
                    ]
                }
            )
        elif query.business_object_ids is not None and len(query.business_object_ids) > 1:
            conditions.append(
                {
                    "key": "business_object_id",
                    "match": {"any": list(query.business_object_ids)},
                }
            )
        else:
            conditions.append(
                {
                    "key": "business_object_id",
                    "match": {"value": query.business_object_id},
                }
            )
        if "all" not in query.evidence_types and len(query.evidence_types) == 1:
            conditions.append(
                {
                    "key": "evidence_type",
                    "match": {"value": query.evidence_types[0]},
                }
            )
        elif "all" not in query.evidence_types:
            conditions.append(
                {
                    "key": "evidence_type",
                    "match": {"any": list(query.evidence_types)},
                }
            )
        if query.business_object_versions is not None:
            pass
        elif query.graph_version_id is not None:
            conditions.append(
                {
                    "key": "graph_version_id",
                    "match": {"value": str(query.graph_version_id)},
                }
            )
        elif query.graph_version is not None:
            conditions.append(
                {"key": "graph_version", "match": {"value": query.graph_version}}
            )
        else:
            conditions.append(
                {
                    "key": "business_version",
                    "match": {"value": query.business_version},
                }
            )
        response = self._request(
            "POST",
            f"/collections/{self.collection_name}/points/search",
            json={
                "vector": vector,
                "filter": {"must": conditions},
                "limit": query.top_k,
                "with_payload": True,
                "with_vector": False,
            },
        )
        result = self._json(response).get("result")
        if not isinstance(result, list):
            raise EvidenceRagError(
                "QDRANT_RESPONSE_INVALID", "Qdrant search response is invalid"
            )
        hits = tuple(self._search_hit(item, query) for item in result)
        return tuple(sorted(hits, key=lambda hit: (-hit.score, hit.evidence_id)))

    def citations(
        self, query: EvidenceCitationQuery
    ) -> tuple[EvidenceRagHit, ...]:
        self._require_initialized()
        conditions: list[dict[str, object]] = [
            {"key": "evidence_id", "match": {"value": query.evidence_id}},
            {"key": "source_version", "match": {"value": query.source_version}},
            {"key": "active", "match": {"value": True}},
        ]
        if query.graph_version_id is not None:
            conditions.append(
                {
                    "key": "graph_version_id",
                    "match": {"value": str(query.graph_version_id)},
                }
            )
        elif query.graph_version is not None:
            conditions.append(
                {"key": "graph_version", "match": {"value": query.graph_version}}
            )
        elif query.business_version is not None:
            conditions.append(
                {
                    "key": "business_version",
                    "match": {"value": query.business_version},
                }
            )
        response = self._request(
            "POST",
            f"/collections/{self.collection_name}/points/scroll",
            json={
                "limit": 100,
                "with_payload": True,
                "with_vector": False,
                "filter": {"must": conditions},
            },
        )
        result = self._json(response).get("result")
        if not isinstance(result, Mapping) or not isinstance(result.get("points"), list):
            raise EvidenceRagError(
                "QDRANT_RESPONSE_INVALID", "Qdrant citation response is invalid"
            )
        return tuple(self._citation_hit(item) for item in result["points"])

    def count(
        self,
        *,
        business_object_type: str,
        business_object_id: str,
        graph_version_id: int | None = None,
        graph_version: str | None = None,
        business_version: str | None = None,
        active_only: bool = True,
    ) -> int:
        self._require_initialized()
        conditions: list[dict[str, object]] = [
            {
                "key": "business_object_type",
                "match": {"value": business_object_type},
            },
            {
                "key": "business_object_id",
                "match": {"value": business_object_id},
            },
        ]
        if active_only:
            conditions.append({"key": "active", "match": {"value": True}})
        if graph_version_id is not None:
            conditions.append(
                {
                    "key": "graph_version_id",
                    "match": {"value": str(graph_version_id)},
                }
            )
        elif graph_version is not None:
            conditions.append(
                {"key": "graph_version", "match": {"value": graph_version}}
            )
        elif business_version is not None:
            conditions.append(
                {
                    "key": "business_version",
                    "match": {"value": business_version},
                }
            )
        response = self._request(
            "POST",
            f"/collections/{self.collection_name}/points/count",
            json={"filter": {"must": conditions}, "exact": True},
        )
        body = self._json(response)
        result = body.get("result")
        if not isinstance(result, Mapping) or not isinstance(result.get("count"), int):
            raise EvidenceRagError(
                "QDRANT_RESPONSE_INVALID", "Qdrant count response is invalid"
            )
        return int(result["count"])

    def deactivate(
        self,
        *,
        tenant_ref: str,
        permission_scope: str,
        source_object_type: str | None = None,
        source_object_id: str | None = None,
        source_document_id: str | None = None,
        source_version: str | None = None,
    ) -> None:
        self._set_active(
            tenant_ref=tenant_ref,
            permission_scope=permission_scope,
            source_object_type=source_object_type,
            source_object_id=source_object_id,
            source_document_id=source_document_id,
            source_version=source_version,
            active=False,
        )

    def delete(
        self,
        *,
        tenant_ref: str,
        permission_scope: str,
        source_object_type: str | None = None,
        source_object_id: str | None = None,
        source_document_id: str | None = None,
        source_version: str | None = None,
    ) -> None:
        self._require_initialized()
        selector = {
            "filter": {
                "must": self._scope_conditions(
                    tenant_ref=tenant_ref,
                    permission_scope=permission_scope,
                    source_object_type=source_object_type,
                    source_object_id=source_object_id,
                    source_document_id=source_document_id,
                    source_version=source_version,
                )
            }
        }
        self._request(
            "POST",
            f"/collections/{self.collection_name}/points/delete",
            params={"wait": "true"},
            json=selector,
        )

    def _set_active(
        self,
        *,
        tenant_ref: str,
        permission_scope: str,
        source_object_type: str | None,
        source_object_id: str | None,
        source_document_id: str | None,
        source_version: str | None,
        active: bool,
    ) -> None:
        self._require_initialized()
        selector = {
            "filter": {
                "must": self._scope_conditions(
                    tenant_ref=tenant_ref,
                    permission_scope=permission_scope,
                    source_object_type=source_object_type,
                    source_object_id=source_object_id,
                    source_document_id=source_document_id,
                    source_version=source_version,
                )
            }
        }
        self._request(
            "POST",
            f"/collections/{self.collection_name}/points/payload",
            params={"wait": "true"},
            json={"payload": {"active": active}, **selector},
        )

    def _find_point_id(
        self,
        *,
        tenant_ref: str,
        permission_scope: str,
        evidence_id: str,
        source_version: str,
        graph_version_id: int | None = None,
        graph_version: str | None = None,
        business_version: str | None = None,
    ) -> str | None:
        self._require_initialized()
        conditions = [
            {"key": "tenant_ref", "match": {"value": tenant_ref}},
            {"key": "permission_scope", "match": {"value": permission_scope}},
            {"key": "evidence_id", "match": {"value": evidence_id}},
            {"key": "source_version", "match": {"value": source_version}},
            {"key": "active", "match": {"value": True}},
        ]
        if graph_version_id is not None:
            conditions.append(
                {
                    "key": "graph_version_id",
                    "match": {"value": str(graph_version_id)},
                }
            )
        elif graph_version is not None:
            conditions.append(
                {"key": "graph_version", "match": {"value": graph_version}}
            )
        elif business_version is not None:
            conditions.append(
                {
                    "key": "business_version",
                    "match": {"value": business_version},
                }
            )
        response = self._request(
            "POST",
            f"/collections/{self.collection_name}/points/scroll",
            json={
                "limit": 5,
                "with_payload": False,
                "with_vector": False,
                "filter": {"must": conditions},
            },
        )
        result = self._json(response).get("result")
        if not isinstance(result, Mapping) or not isinstance(result.get("points"), list):
            raise EvidenceRagError(
                "QDRANT_RESPONSE_INVALID", "Qdrant scroll response is invalid"
            )
        for point in result["points"]:
            if isinstance(point, Mapping) and point.get("id") is not None:
                return str(point["id"])
        return None

    def _find_point_ids(
        self, records: tuple[EvidenceRagRecord, ...]
    ) -> dict[tuple[str, str, str, str, str], str]:
        grouped: dict[tuple[str, str], list[EvidenceRagRecord]] = {}
        for record in records:
            grouped.setdefault(
                (record.tenant_ref, record.permission_scope), []
            ).append(record)
        point_ids: dict[tuple[str, str, str, str, str], str] = {}
        for (tenant_ref, permission_scope), group in grouped.items():
            evidence_ids = sorted({record.evidence_id for record in group})
            conditions = [
                {"key": "tenant_ref", "match": {"value": tenant_ref}},
                {
                    "key": "permission_scope",
                    "match": {"value": permission_scope},
                },
                {"key": "evidence_id", "match": {"any": evidence_ids}},
                {"key": "active", "match": {"value": True}},
            ]
            next_offset: object = None
            while True:
                payload: dict[str, object] = {
                    "limit": 1000,
                    "with_payload": [
                        "evidence_id",
                        "source_version",
                        "graph_version_id",
                        "graph_version",
                        "business_version",
                    ],
                    "with_vector": False,
                    "filter": {"must": conditions},
                }
                if next_offset is not None:
                    payload["offset"] = next_offset
                response = self._request(
                    "POST",
                    f"/collections/{self.collection_name}/points/scroll",
                    json=payload,
                )
                body = self._json(response)
                result = body.get("result")
                if not isinstance(result, Mapping) or not isinstance(
                    result.get("points"), list
                ):
                    raise EvidenceRagError(
                        "QDRANT_RESPONSE_INVALID",
                        "Qdrant scroll response is invalid",
                    )
                for point in result["points"]:
                    if not isinstance(point, Mapping) or point.get("id") is None:
                        continue
                    point_payload = point.get("payload")
                    if not isinstance(point_payload, Mapping):
                        continue
                    key = _point_identity(
                        evidence_id=point_payload.get("evidence_id"),
                        source_version=point_payload.get("source_version"),
                        graph_version_id=point_payload.get("graph_version_id"),
                        graph_version=point_payload.get("graph_version"),
                        business_version=point_payload.get("business_version"),
                    )
                    point_ids.setdefault(key, str(point["id"]))
                next_offset = result.get("next_page_offset")
                if next_offset is None:
                    break
        return point_ids

    @staticmethod
    def _scope_conditions(
        *,
        tenant_ref: str,
        permission_scope: str,
        source_object_type: str | None,
        source_object_id: str | None,
        source_document_id: str | None,
        source_version: str | None,
    ) -> list[dict[str, object]]:
        conditions: list[dict[str, object]] = [
            {"key": "tenant_ref", "match": {"value": tenant_ref}},
            {"key": "permission_scope", "match": {"value": permission_scope}},
        ]
        optional = {
            "source_object_type": source_object_type,
            "source_object_id": source_object_id,
            "source_document_id": source_document_id,
            "source_version": source_version,
        }
        for key, value in optional.items():
            if value is not None:
                conditions.append({"key": key, "match": {"value": value}})
        return conditions

    def _payload(self, record: EvidenceRagRecord) -> dict[str, object]:
        return {
            "tenant_ref": record.tenant_ref,
            "permission_scope": record.permission_scope,
            "active": True,
            "business_object_type": record.business_object_type,
            "business_object_id": record.business_object_id,
            "business_object_name": record.business_object_name,
            "evidence_type": record.evidence_type,
            "evidence_id": record.evidence_id,
            "source_object_type": record.source_object_type,
            "source_object_id": record.source_object_id,
            "source_document_id": record.source_document_id,
            "source_version": record.source_version,
            "quote": record.quote,
            "location_start": record.location_start,
            "location_end": record.location_end,
            "occurrence_index": record.occurrence_index,
            "alignment": record.alignment,
            "graph_version_id": (
                str(record.graph_version_id)
                if record.graph_version_id is not None
                else None
            ),
            "graph_version": record.graph_version,
            "business_version": record.business_version,
            "text": record.text,
        }

    def _search_hit(
        self, raw: object, query: EvidenceRagQuery
    ) -> EvidenceRagHit:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("payload"), Mapping):
            raise EvidenceRagError(
                "QDRANT_RESPONSE_INVALID", "Qdrant returned an invalid search hit"
            )
        payload = raw["payload"]
        visible = (
            payload.get("tenant_ref") == query.tenant_ref
            and payload.get("permission_scope") == query.permission_scope
        ) or (
            payload.get("tenant_ref") == PLATFORM_PUBLIC_TENANT_REF
            and payload.get("permission_scope") == PLATFORM_PERMISSION_SCOPE
        )
        if not visible:
            if payload.get("tenant_ref") != query.tenant_ref:
                raise EvidenceRagError(
                    "QDRANT_TENANT_VIOLATION",
                    "Qdrant returned a cross-tenant Evidence hit",
                )
            raise EvidenceRagError(
                "QDRANT_FILTER_VIOLATION",
                "Qdrant returned Evidence outside the permission scope",
            )
        if payload.get("active") is not True:
            raise EvidenceRagError(
                "QDRANT_FILTER_VIOLATION",
                "Qdrant returned an inactive Evidence point",
            )
        if payload.get("business_object_type") != query.business_object_type:
            raise EvidenceRagError(
                "QDRANT_FILTER_VIOLATION",
                "Qdrant returned Evidence outside the business object filter",
            )
        if query.business_object_ids is not None and len(query.business_object_ids) > 1:
            if payload.get("business_object_id") not in set(
                query.business_object_ids
            ):
                raise EvidenceRagError(
                    "QDRANT_FILTER_VIOLATION",
                    "Qdrant returned Evidence outside the business object filter",
                )
        elif payload.get("business_object_id") != query.business_object_id:
            raise EvidenceRagError(
                "QDRANT_FILTER_VIOLATION",
                "Qdrant returned Evidence outside the business object filter",
            )
        evidence_type = payload.get("evidence_type")
        if "all" not in query.evidence_types and evidence_type not in query.evidence_types:
            raise EvidenceRagError(
                "QDRANT_FILTER_VIOLATION",
                "Qdrant returned Evidence outside the evidence type filter",
            )
        self._validate_version(payload, query)
        try:
            return EvidenceRagHit(
                evidence_id=str(payload["evidence_id"]),
                business_object_id=str(payload["business_object_id"]),
                source_object_type=str(payload["source_object_type"]),
                source_object_id=str(payload["source_object_id"]),
                source_document_id=str(payload["source_document_id"]),
                source_version=str(payload["source_version"]),
                score=float(raw["score"]),
                quote=_nullable_str(payload.get("quote")),
                location_start=_nullable_int(payload.get("location_start")),
                location_end=_nullable_int(payload.get("location_end")),
                occurrence_index=_nullable_int(payload.get("occurrence_index")),
                alignment=_nullable_str(payload.get("alignment")) or "unresolved",
                graph_version_id=_nullable_int(payload.get("graph_version_id")),
                graph_version=_nullable_str(payload.get("graph_version")),
                business_version=_nullable_str(payload.get("business_version")),
                tenant_ref=str(payload["tenant_ref"]),
                permission_scope=str(payload["permission_scope"]),
                business_object_name=_nullable_str(
                    payload.get("business_object_name")
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise EvidenceRagError(
                "QDRANT_RESPONSE_INVALID",
                "Qdrant returned an invalid Evidence point payload",
            ) from exc

    def _citation_hit(self, raw: object) -> EvidenceRagHit:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("payload"), Mapping):
            raise EvidenceRagError(
                "QDRANT_RESPONSE_INVALID", "Qdrant returned an invalid citation"
            )
        payload = raw["payload"]
        if payload.get("active") is not True:
            raise EvidenceRagError(
                "QDRANT_FILTER_VIOLATION", "Qdrant returned an inactive citation"
            )
        try:
            quote = _nullable_str(payload.get("quote"))
            start = _nullable_int(payload.get("location_start"))
            end = _nullable_int(payload.get("location_end"))
            source_text = _nullable_str(payload.get("text")) or ""
            highlight = quote
            if not highlight and start is not None and end is not None:
                highlight = source_text[start:end]
            return EvidenceRagHit(
                evidence_id=str(payload["evidence_id"]),
                business_object_id=str(payload["business_object_id"]),
                source_object_type=str(payload["source_object_type"]),
                source_object_id=str(payload["source_object_id"]),
                source_document_id=str(payload["source_document_id"]),
                source_version=str(payload["source_version"]),
                score=1.0,
                quote=quote,
                location_start=start,
                location_end=end,
                occurrence_index=_nullable_int(payload.get("occurrence_index")),
                alignment=_nullable_str(payload.get("alignment")) or "unresolved",
                graph_version_id=_nullable_int(payload.get("graph_version_id")),
                graph_version=_nullable_str(payload.get("graph_version")),
                business_version=_nullable_str(payload.get("business_version")),
                tenant_ref=str(payload["tenant_ref"]),
                permission_scope=str(payload["permission_scope"]),
                business_object_name=_nullable_str(payload.get("business_object_name")),
                highlight_text=highlight,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise EvidenceRagError(
                "QDRANT_RESPONSE_INVALID", "Qdrant citation payload is invalid"
            ) from exc

    @staticmethod
    def _validate_version(payload: Mapping[str, object], query: EvidenceRagQuery) -> None:
        if query.business_object_versions is not None:
            expected_versions = dict(query.business_object_versions)
            object_id = str(payload.get("business_object_id") or "")
            expected = expected_versions.get(object_id)
            if expected is None or payload.get("graph_version_id") != str(expected):
                raise EvidenceRagError(
                    "QDRANT_FILTER_VIOLATION",
                    "Qdrant returned Evidence outside the business object graph version filter",
                )
            return
        if query.graph_version_id is not None:
            if payload.get("graph_version_id") != str(query.graph_version_id):
                raise EvidenceRagError(
                    "QDRANT_FILTER_VIOLATION",
                    "Qdrant returned Evidence outside the graph version filter",
                )
            return
        if query.graph_version is not None:
            if payload.get("graph_version") != query.graph_version:
                raise EvidenceRagError(
                    "QDRANT_FILTER_VIOLATION",
                    "Qdrant returned Evidence outside the graph version filter",
                )
            return
        if payload.get("business_version") != query.business_version:
            raise EvidenceRagError(
                "QDRANT_FILTER_VIOLATION",
                "Qdrant returned Evidence outside the business version filter",
            )

    def _validate_vector_schema(self, body: Mapping[str, object]) -> None:
        result = body.get("result")
        if not isinstance(result, Mapping):
            raise EvidenceRagError(
                "QDRANT_SCHEMA_INVALID", "Qdrant collection schema is incomplete"
            )
        try:
            params = result["config"]["params"]
            vectors = params["vectors"]
            size = int(vectors["size"])
            distance = str(vectors["distance"]).lower()
        except (KeyError, TypeError, ValueError) as exc:
            raise EvidenceRagError(
                "QDRANT_SCHEMA_INVALID", "Qdrant collection schema is incomplete"
            ) from exc
        if size != self.dimension or distance != "cosine":
            raise EvidenceRagError(
                "QDRANT_SCHEMA_MISMATCH",
                "Qdrant collection vector schema does not match configuration",
            )

    def _require_initialized(self) -> None:
        if not self._initialized:
            self.initialize()

    def _request(
        self,
        method: str,
        path: str,
        *,
        accepted_statuses: set[int] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        accepted = accepted_statuses or {200}
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.request(method, path, **kwargs)
            except httpx.TimeoutException as exc:
                if attempt < self._max_retries:
                    self._wait(attempt)
                    continue
                raise EvidenceRagError(
                    "QDRANT_TIMEOUT", "Qdrant request timed out"
                ) from exc
            except httpx.RequestError as exc:
                if attempt < self._max_retries:
                    self._wait(attempt)
                    continue
                raise EvidenceRagError(
                    "QDRANT_UNAVAILABLE", "Qdrant is unavailable"
                ) from exc
            if response.status_code in accepted:
                return response
            if (response.status_code == 429 or response.status_code >= 500) and (
                attempt < self._max_retries
            ):
                self._wait(attempt)
                continue
            code = (
                "QDRANT_UNAVAILABLE"
                if response.status_code == 429 or response.status_code >= 500
                else "QDRANT_REQUEST_REJECTED"
            )
            raise EvidenceRagError(
                code, f"Qdrant returned HTTP {response.status_code}"
            )
        raise AssertionError("Qdrant retry loop must return or raise")

    def _json(self, response: httpx.Response) -> Mapping[str, object]:
        try:
            body = response.json()
        except ValueError as exc:
            raise EvidenceRagError(
                "QDRANT_RESPONSE_INVALID", "Qdrant returned invalid JSON"
            ) from exc
        if not isinstance(body, Mapping):
            raise EvidenceRagError(
                "QDRANT_RESPONSE_INVALID", "Qdrant returned an invalid response"
            )
        return body

    def _wait(self, attempt: int) -> None:
        delay = self._retry_backoff * (2**attempt)
        if delay:
            time.sleep(delay)


def _nullable_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _nullable_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


__all__ = ["QdrantEvidenceRagStore"]
