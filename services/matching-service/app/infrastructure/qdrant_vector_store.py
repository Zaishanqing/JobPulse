"""Qdrant REST implementation of the technology-neutral VectorStorePort."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

import httpx

from app.domain.privacy import find_pii
from app.domain.vector_contracts import (
    SemanticFragment,
    VectorContractViolation,
    VectorIndexReference,
    VectorPointSnapshot,
    VectorQuery,
    VectorRecord,
    VectorSearchHit,
    deterministic_point_id,
)

DEFAULT_COLLECTION = "matching_fragments_v1"
DEFAULT_DIMENSION = 1024
_KEYWORD_INDEXES = (
    "tenant_ref",
    "entity_type",
    "entity_id",
    "fragment_type",
    "target_type",
    "profile_version",
    "profile_fingerprint",
    "embedding_model",
    "embedding_revision",
    "normalized",
    "normalization",
    "representation",
    "similarity",
    "text_derivation_version",
    "collection",
    "index_revision",
    "grant_id",
    "personal_tenant_ref",
    "enterprise_tenant_ref",
)
_BOOL_INDEXES = ("active",)


class QdrantVectorStoreAdapter:
    def __init__(
        self,
        url: str,
        *,
        api_key: str | None = None,
        collection_name: str = DEFAULT_COLLECTION,
        index_revision: str | None = None,
        dimension: int = DEFAULT_DIMENSION,
        timeout_seconds: float = 5.0,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.1,
        client: httpx.Client | None = None,
        initialize: bool = True,
    ) -> None:
        if not url.strip() or not collection_name.strip():
            raise ValueError("Qdrant URL and collection name are required")
        if dimension <= 0 or timeout_seconds <= 0:
            raise ValueError("Qdrant dimension and timeout must be positive")
        if max_retries < 0 or retry_backoff_seconds < 0:
            raise ValueError("Qdrant retry configuration cannot be negative")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if api_key:
            headers["api-key"] = api_key
        self._client = client or httpx.Client(
            base_url=url.rstrip("/"), timeout=timeout_seconds, headers=headers
        )
        self._owns_client = client is None
        self.collection_name = collection_name
        self.index_revision = index_revision or collection_name
        self.dimension = dimension
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff_seconds
        self._initialized = False
        if initialize:
            self.initialize()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def health(self) -> None:
        self._request("GET", "/healthz", expect_json=False)

    def check_health(self) -> None:
        self.health()

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
            _result, payload_schema = self._collection_schema_parts(body)
            self._validate_present_payload_schema(payload_schema)
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
        schema_response = self._request("GET", path)
        self._validate_collection_schema(self._json(schema_response))
        self._initialized = True

    def check_startup_contract(self) -> None:
        """Re-run the non-destructive collection/schema contract check."""
        self.initialize()

    def upsert(self, records: tuple[VectorRecord, ...]) -> tuple[VectorIndexReference, ...]:
        self._require_initialized()
        if not records:
            return ()
        points: list[dict[str, object]] = []
        for record in records:
            if record.dimension != self.dimension:
                raise VectorContractViolation(
                    "QDRANT_DIMENSION_MISMATCH",
                    "vector record dimension does not match the collection",
                )
            if find_pii(record.model_dump(mode="python")):
                raise VectorContractViolation(
                    "QDRANT_PII_FORBIDDEN", "vector record contains prohibited PII"
                )
            if (
                record.index_revision != self.index_revision
                or record.collection != self.collection_name
                or not record.normalized
                or record.normalization != "l2"
                or record.representation != "dense"
                or record.similarity != "cosine"
            ):
                raise VectorContractViolation(
                    "QDRANT_LINEAGE_MISMATCH",
                    "vector record lineage does not match the configured collection",
                )
            points.append(
                {
                    "id": record.point_id,
                    "vector": list(record.embedding),
                    "payload": self._payload(record),
                }
            )
        self._request(
            "PUT",
            f"/collections/{self.collection_name}/points",
            params={"wait": "true"},
            json={"points": points},
        )
        indexed_at = datetime.now(timezone.utc)
        return tuple(
            VectorIndexReference(
                index_name=self.collection_name,
                tenant_ref=record.tenant_ref,
                point_id=record.point_id,
                fragment_id=record.fragment.fragment_id,
                active=record.active,
                indexed_at=indexed_at,
            )
            for record in records
        )

    def search(self, query: VectorQuery) -> tuple[VectorSearchHit, ...]:
        self._require_initialized()
        if (
            query.collection is not None
            and query.collection != self.collection_name
        ) or (
            query.index_revision is not None
            and query.index_revision != self.index_revision
        ):
            raise VectorContractViolation(
                "QDRANT_LINEAGE_MISMATCH",
                "vector query lineage does not match the configured collection",
            )
        if query.dimension != self.dimension:
            raise VectorContractViolation(
                "QDRANT_DIMENSION_MISMATCH",
                "query dimension does not match the collection",
            )
        conditions: list[dict[str, object]] = [
            {"key": "tenant_ref", "match": {"value": query.tenant_ref}},
            {"key": "active", "match": {"value": True}},
            {"key": "embedding_model", "match": {"value": query.embedding_model}},
            {
                "key": "embedding_revision",
                "match": {"value": query.embedding_revision},
            },
            {"key": "normalized", "match": {"value": True}},
            {"key": "normalization", "match": {"value": "l2"}},
            {"key": "representation", "match": {"value": "dense"}},
            {"key": "similarity", "match": {"value": "cosine"}},
            {
                "key": "text_derivation_version",
                "match": {"value": query.text_derivation_version},
            },
            {"key": "collection", "match": {"value": self.collection_name}},
        ]
        if query.index_revision is not None:
            conditions.append(
                {"key": "index_revision", "match": {"value": query.index_revision}}
            )
        if query.filter.profile_version is not None:
            conditions.append(
                {
                    "key": "profile_version",
                    "match": {"value": query.filter.profile_version},
                }
            )
        if query.filter.fragment_types:
            conditions.append(
                {
                    "key": "fragment_type",
                    "match": {"any": list(query.filter.fragment_types)},
                }
            )
        if query.filter.source_ids:
            conditions.append({"key": "entity_id", "match": {"any": list(query.filter.source_ids)}})
        if query.filter.target_types:
            conditions.append(
                {
                    "key": "target_type",
                    "match": {"any": list(query.filter.target_types)},
                }
            )
        response = self._request(
            "POST",
            f"/collections/{self.collection_name}/points/search",
            json={
                "vector": list(query.embedding),
                "filter": {"must": conditions},
                "limit": query.top_k,
                "with_payload": True,
                "with_vector": False,
            },
        )
        result = self._json(response).get("result")
        if not isinstance(result, list):
            raise VectorContractViolation(
                "QDRANT_RESPONSE_INVALID", "Qdrant search response is invalid"
            )
        hits = tuple(self._search_hit(item, query) for item in result)
        return tuple(sorted(hits, key=lambda item: (-item.score, item.point_id)))

    def deactivate(self, *, tenant_ref: str, point_ids: tuple[str, ...]) -> None:
        self._set_inactive_or_delete(tenant_ref=tenant_ref, point_ids=point_ids, delete=False)

    def activate(self, *, tenant_ref: str, point_ids: tuple[str, ...]) -> None:
        self._set_active(tenant_ref=tenant_ref, point_ids=point_ids)

    def delete(self, *, tenant_ref: str, point_ids: tuple[str, ...]) -> None:
        self._set_inactive_or_delete(tenant_ref=tenant_ref, point_ids=point_ids, delete=True)

    def list_points(self, *, tenant_ref=None, embedding_revision=None):
        self._require_initialized()
        conditions: list[dict[str, object]] = []
        if tenant_ref is not None:
            conditions.append({"key": "tenant_ref", "match": {"value": tenant_ref}})
        if embedding_revision is not None:
            conditions.append({"key": "embedding_revision", "match": {"value": embedding_revision}})
        body: dict[str, object] = {
            "limit": 256,
            "with_payload": True,
            "with_vector": False,
        }
        if conditions:
            body["filter"] = {"must": conditions}
        points: list[VectorPointSnapshot] = []
        while True:
            response = self._request(
                "POST",
                f"/collections/{self.collection_name}/points/scroll",
                json=body,
            )
            result = self._json(response).get("result")
            if not isinstance(result, Mapping) or not isinstance(result.get("points"), list):
                raise VectorContractViolation(
                    "QDRANT_RESPONSE_INVALID", "Qdrant scroll response is invalid"
                )
            points.extend(self._point_snapshot(item, tenant_ref) for item in result["points"])
            offset = result.get("next_page_offset")
            if offset is None:
                break
            body["offset"] = offset
        return tuple(sorted(points, key=lambda item: (item.tenant_ref, item.point_id)))

    def _set_inactive_or_delete(
        self, *, tenant_ref: str, point_ids: tuple[str, ...], delete: bool
    ) -> None:
        self._require_initialized()
        if not point_ids:
            return
        selector = {
            "filter": {
                "must": [
                    {"key": "tenant_ref", "match": {"value": tenant_ref}},
                    {"has_id": list(point_ids)},
                ]
            }
        }
        if delete:
            self._request(
                "POST",
                f"/collections/{self.collection_name}/points/delete",
                params={"wait": "true"},
                json=selector,
            )
        else:
            self._request(
                "POST",
                f"/collections/{self.collection_name}/points/payload",
                params={"wait": "true"},
                json={"payload": {"active": False}, **selector},
            )

    def _set_active(self, *, tenant_ref: str, point_ids: tuple[str, ...]) -> None:
        self._require_initialized()
        if not point_ids:
            return
        self._request(
            "POST",
            f"/collections/{self.collection_name}/points/payload",
            params={"wait": "true"},
            json={
                "payload": {"active": True},
                "filter": {
                    "must": [
                        {"key": "tenant_ref", "match": {"value": tenant_ref}},
                        {"has_id": list(point_ids)},
                    ]
                },
            },
        )

    def _payload(self, record: VectorRecord) -> dict[str, object]:
        fragment = record.fragment
        return {
            "tenant_ref": record.tenant_ref,
            "entity_type": fragment.source_type,
            "entity_id": fragment.source_id,
            "fragment_id": fragment.fragment_id,
            "fragment_type": fragment.fragment_type,
            "target_type": fragment.target_type,
            "profile_version": fragment.source_profile_id,
            "embedding_model": record.embedding_model,
            "embedding_revision": record.embedding_revision,
            "normalized": record.normalized,
            "normalization": record.normalization,
            "representation": record.representation,
            "similarity": record.similarity,
            "text_derivation_version": record.text_derivation_version,
            "collection": self.collection_name,
            "index_revision": self.index_revision,
            "active": record.active,
            "grant_id": fragment.grant_id,
            "grant_version": fragment.grant_version,
            "personal_tenant_ref": fragment.personal_tenant_ref,
            "enterprise_tenant_ref": fragment.enterprise_tenant_ref,
            "fragment": fragment.model_dump(
                mode="json",
                exclude=set(),
            ),
            "metadata": record.payload,
        }

    @staticmethod
    def _point_snapshot(raw: object, expected_tenant: str | None) -> VectorPointSnapshot:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("payload"), Mapping):
            raise VectorContractViolation(
                "QDRANT_RESPONSE_INVALID", "Qdrant returned an invalid point"
            )
        payload = raw["payload"]
        if expected_tenant is not None and payload.get("tenant_ref") != expected_tenant:
            raise VectorContractViolation(
                "QDRANT_TENANT_VIOLATION", "Qdrant returned a cross-tenant point"
            )
        try:
            return VectorPointSnapshot(
                point_id=str(raw["id"]),
                tenant_ref=str(payload["tenant_ref"]),
                entity_type=payload["entity_type"],
                entity_id=str(payload["entity_id"]),
                fragment_id=str(payload["fragment_id"]),
                profile_version=str(payload["profile_version"]),
                embedding_revision=str(payload["embedding_revision"]),
                active=payload["active"],
            )
        except VectorContractViolation:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise VectorContractViolation(
                "QDRANT_RESPONSE_INVALID", "Qdrant returned an invalid point payload"
            ) from exc

    def _search_hit(self, raw: object, query: VectorQuery) -> VectorSearchHit:
        if not isinstance(raw, Mapping):
            raise VectorContractViolation(
                "QDRANT_RESPONSE_INVALID", "Qdrant returned an invalid search hit"
            )
        payload = raw.get("payload")
        if not isinstance(payload, Mapping) or payload.get("tenant_ref") != query.tenant_ref:
            raise VectorContractViolation(
                "QDRANT_TENANT_VIOLATION", "Qdrant returned a cross-tenant search hit"
            )
        expected_values = {
            "active": True,
            "embedding_model": query.embedding_model,
            "embedding_revision": query.embedding_revision,
            "normalized": True,
            "normalization": "l2",
            "representation": "dense",
            "similarity": "cosine",
            "text_derivation_version": query.text_derivation_version,
            "collection": self.collection_name,
        }
        if (
            query.index_revision is not None
            and payload.get("index_revision") != query.index_revision
        ):
            raise VectorContractViolation(
                "VECTOR_INDEX_REVISION_MISMATCH",
                "Qdrant returned a hit from a different index revision",
            )
        if any(payload.get(key) != value for key, value in expected_values.items()):
            raise VectorContractViolation(
                "QDRANT_FILTER_VIOLATION", "Qdrant returned a hit outside query filters"
            )
        if (
            query.filter.profile_version is not None
            and payload.get("profile_version") != query.filter.profile_version
        ):
            raise VectorContractViolation(
                "QDRANT_FILTER_VIOLATION", "Qdrant returned a hit outside query filters"
            )
        if query.filter.fragment_types and payload.get("fragment_type") not in (
            query.filter.fragment_types
        ):
            raise VectorContractViolation(
                "QDRANT_FILTER_VIOLATION", "Qdrant returned a hit outside query filters"
            )
        if query.filter.source_ids and payload.get("entity_id") not in (query.filter.source_ids):
            raise VectorContractViolation(
                "QDRANT_FILTER_VIOLATION", "Qdrant returned a hit outside query filters"
            )
        if query.filter.target_types and payload.get("target_type") not in (
            query.filter.target_types
        ):
            raise VectorContractViolation(
                "QDRANT_FILTER_VIOLATION", "Qdrant returned a hit outside query filters"
            )
        try:
            fragment = SemanticFragment.model_validate(payload.get("fragment"))
            if (
                fragment.source_id != payload.get("entity_id")
                or fragment.fragment_id != payload.get("fragment_id")
                or fragment.fragment_type != payload.get("fragment_type")
                or fragment.target_type != payload.get("target_type")
                or fragment.source_profile_id != payload.get("profile_version")
            ):
                raise ValueError("fragment lineage does not match indexed payload")
            expected_point_id = deterministic_point_id(
                fragment,
                embedding_model=query.embedding_model,
                embedding_revision=query.embedding_revision,
                dimension=query.dimension,
            )
            if str(raw["id"]) != expected_point_id:
                raise VectorContractViolation(
                    "QDRANT_LINEAGE_VIOLATION",
                    "point identity does not match immutable embedding lineage",
                )
            metadata = payload.get("metadata", {})
            if not isinstance(metadata, dict):
                raise ValueError("metadata is not an object")
            score = float(raw["score"])
            if not math.isfinite(score):
                raise ValueError("vector search score must be finite")
            score = max(-1.0, min(1.0, score))
            return VectorSearchHit(
                point_id=str(raw["id"]),
                tenant_ref=query.tenant_ref,
                fragment=fragment,
                score=score,
                payload=metadata,
            )
        except VectorContractViolation:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise VectorContractViolation(
                "QDRANT_RESPONSE_INVALID", "Qdrant returned an invalid search hit"
            ) from exc

    def _validate_collection_schema(self, body: Mapping[str, object]) -> None:
        result, payload_schema = self._collection_schema_parts(body)
        self._validate_vector_schema(body)
        del result
        expected = {
            **dict.fromkeys(_KEYWORD_INDEXES, "keyword"),
            **dict.fromkeys(_BOOL_INDEXES, "bool"),
        }
        for field_name, data_type in expected.items():
            item = payload_schema.get(field_name)
            actual = item.get("data_type") if isinstance(item, Mapping) else None
            if str(actual).lower() != data_type:
                raise VectorContractViolation(
                    "QDRANT_SCHEMA_MISMATCH",
                    "QDRANT_COLLECTION_SCHEMA_MISMATCH: "
                    f"Qdrant payload index {field_name} does not match configuration",
                )

    def _validate_present_payload_schema(self, payload_schema: Mapping[str, object]) -> None:
        expected = {
            **dict.fromkeys(_KEYWORD_INDEXES, "keyword"),
            **dict.fromkeys(_BOOL_INDEXES, "bool"),
        }
        for field_name, item in payload_schema.items():
            if field_name not in expected:
                continue
            actual = item.get("data_type") if isinstance(item, Mapping) else None
            if str(actual).lower() != expected[field_name]:
                raise VectorContractViolation(
                    "QDRANT_SCHEMA_MISMATCH",
                    "QDRANT_COLLECTION_SCHEMA_MISMATCH: "
                    f"Qdrant payload index {field_name} does not match configuration",
                )

    def _validate_vector_schema(self, body: Mapping[str, object]) -> None:
        result, _payload_schema = self._collection_schema_parts(body)
        try:
            config = result["config"]
            if not isinstance(config, Mapping):
                raise KeyError("config")
            params = config["params"]
            if not isinstance(params, Mapping):
                raise KeyError("params")
            vectors = params["vectors"]
            if not isinstance(vectors, Mapping):
                raise KeyError("vectors")
            size = int(vectors["size"])
            distance = str(vectors["distance"]).lower()
        except (KeyError, TypeError, ValueError) as exc:
            raise VectorContractViolation(
                "QDRANT_SCHEMA_INVALID", "Qdrant collection schema is incomplete"
            ) from exc
        if size != self.dimension or distance != "cosine":
            raise VectorContractViolation(
                "QDRANT_SCHEMA_MISMATCH",
                "QDRANT_COLLECTION_SCHEMA_MISMATCH: "
                "Qdrant collection vector schema does not match configuration",
            )

    def _collection_schema_parts(
        self, body: Mapping[str, object]
    ) -> tuple[Mapping[str, object], Mapping[str, object]]:
        result = body.get("result")
        if not isinstance(result, Mapping):
            raise VectorContractViolation(
                "QDRANT_SCHEMA_INVALID", "Qdrant collection schema is incomplete"
            )
        payload_schema = result.get("payload_schema")
        if not isinstance(payload_schema, Mapping):
            raise VectorContractViolation(
                "QDRANT_SCHEMA_INVALID", "Qdrant collection schema is incomplete"
            )
        return result, payload_schema

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise VectorContractViolation(
                "QDRANT_NOT_INITIALIZED", "Qdrant collection is not initialized"
            )

    def _request(
        self,
        method: str,
        path: str,
        *,
        accepted_statuses: set[int] | None = None,
        expect_json: bool = True,
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
                raise VectorContractViolation("QDRANT_TIMEOUT", "Qdrant request timed out") from exc
            except httpx.RequestError as exc:
                if attempt < self._max_retries:
                    self._wait(attempt)
                    continue
                raise VectorContractViolation(
                    "QDRANT_UNAVAILABLE", "Qdrant is unavailable"
                ) from exc
            if response.status_code in accepted:
                if expect_json and response.status_code != 404:
                    self._json(response)
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
            raise VectorContractViolation(code, f"Qdrant returned HTTP {response.status_code}")
        raise AssertionError("Qdrant retry loop must return or raise")

    def _json(self, response: httpx.Response) -> Mapping[str, object]:
        try:
            body = response.json()
        except ValueError as exc:
            raise VectorContractViolation(
                "QDRANT_RESPONSE_INVALID", "Qdrant returned invalid JSON"
            ) from exc
        if not isinstance(body, Mapping):
            raise VectorContractViolation(
                "QDRANT_RESPONSE_INVALID", "Qdrant returned an invalid response"
            )
        return body

    def _wait(self, attempt: int) -> None:
        delay = self._retry_backoff * (2**attempt)
        if delay:
            time.sleep(delay)


__all__ = [
    "DEFAULT_COLLECTION",
    "DEFAULT_DIMENSION",
    "QdrantVectorStoreAdapter",
]
