from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping

import httpx
import pytest

from app.domain.profiles import Evidence
from app.domain.vector_contracts import (
    EmbeddingRequest,
    SemanticFragment,
    VectorContractViolation,
    VectorQuery,
    VectorRecord,
)
from app.infrastructure.fake_vector_adapters import FakeEmbeddingAdapter
from app.infrastructure.qdrant_vector_store import (
    DEFAULT_COLLECTION,
    QdrantVectorStoreAdapter,
)


class QdrantStub:
    def __init__(self, *, dimension: int = 4) -> None:
        self.dimension = dimension
        self.exists = False
        self.indexes: dict[str, dict[str, str]] = {}
        self.points: dict[str, dict] = {}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        if path == "/healthz":
            return httpx.Response(200, text="healthz check passed")
        if path == "/collections/matching_fragments_v1" and request.method == "GET":
            if not self.exists:
                return httpx.Response(404, json={"status": "not found"})
            return httpx.Response(200, json=self._schema())
        if path == "/collections/matching_fragments_v1" and request.method == "PUT":
            self.exists = True
            self.dimension = body["vectors"]["size"]
            return httpx.Response(200, json={"result": True, "status": "ok"})
        if path.endswith("/index") and request.method == "PUT":
            self.indexes[body["field_name"]] = {"data_type": body["field_schema"]}
            return httpx.Response(200, json={"result": True, "status": "ok"})
        if path.endswith("/points") and request.method == "PUT":
            for point in body["points"]:
                self.points[point["id"]] = point
            return httpx.Response(200, json={"result": {"status": "completed"}})
        if path.endswith("/points/search") and request.method == "POST":
            must = body["filter"]["must"]
            matches = []
            for point in self.points.values():
                if not self._matches(point["payload"], must):
                    continue
                matches.append(
                    {
                        "id": point["id"],
                        "score": self._cosine(body["vector"], point["vector"]),
                        "payload": point["payload"],
                    }
                )
            matches.sort(key=lambda item: (-item["score"], item["id"]))
            return httpx.Response(200, json={"result": matches[: body["limit"]]})
        if path.endswith("/points/scroll") and request.method == "POST":
            points = [
                {"id": point["id"], "payload": point["payload"]}
                for point in self.points.values()
                if self._matches(point["payload"], body.get("filter", {}).get("must", []))
            ]
            return httpx.Response(
                200,
                json={"result": {"points": points, "next_page_offset": None}},
            )
        if path.endswith("/points/payload") and request.method == "POST":
            for point in self._selected(body["filter"]):
                point["payload"].update(body["payload"])
            return httpx.Response(200, json={"result": {"status": "completed"}})
        if path.endswith("/points/delete") and request.method == "POST":
            selected = {item["id"] for item in self._selected(body["filter"])}
            for point_id in selected:
                self.points.pop(point_id)
            return httpx.Response(200, json={"result": {"status": "completed"}})
        return httpx.Response(500, json={"status": "unexpected request"})

    def _schema(self) -> dict:
        return {
            "result": {
                "config": {"params": {"vectors": {"size": self.dimension, "distance": "Cosine"}}},
                "payload_schema": self.indexes,
            }
        }

    def _selected(self, vector_filter: Mapping[str, object]) -> list[dict]:
        return [
            point
            for point in self.points.values()
            if self._matches(point["payload"], vector_filter["must"])
        ]

    @staticmethod
    def _matches(payload: Mapping[str, object], conditions: list[dict]) -> bool:
        for condition in conditions:
            if "has_id" in condition:
                continue
            match = condition["match"]
            actual = payload.get(condition["key"])
            if "value" in match and actual != match["value"]:
                return False
            if "any" in match and actual not in match["any"]:
                return False
        return True

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        return max(
            -1.0,
            min(
                1.0,
                sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm),
            ),
        )


def _fragment(tenant: str, *, source_id: str = "cv:opaque-1") -> SemanticFragment:
    return SemanticFragment(
        tenant_ref=tenant,
        fragment_id=f"fragment:{source_id}",
        source_type="cv",
        target_type="candidate_cv",
        source_id=source_id,
        source_version="cv.v1",
        source_profile_id="a" * 64,
        fragment_type="skill_context",
        normalized_text="Python services",
        evidence_ref=Evidence(source_id="cv:evidence:1", quote="Python services"),
        language="en",
        sequence=0,
        taxonomy_version="taxonomy.v1",
    )


def _record(
    tenant: str,
    *,
    revision: str = "revision-1",
    source_id: str = "cv:opaque-1",
    dimension: int = 4,
) -> VectorRecord:
    fragment = _fragment(tenant, source_id=source_id)
    request = EmbeddingRequest(
        tenant_ref=tenant,
        embedding_model="fake-model",
        embedding_revision=revision,
        dimension=dimension,
        fragments=(fragment,),
    )
    result = FakeEmbeddingAdapter(model="fake-model", revision=revision, dimension=dimension).embed(
        request
    )
    return VectorRecord.build(
        fragment=fragment,
        embedding=result.vectors[0],
        embedding_model=result.embedding_model,
        embedding_revision=result.embedding_revision,
        index_revision=DEFAULT_COLLECTION,
        collection=DEFAULT_COLLECTION,
        payload={"safe_tag": "test"},
    )


def _query(record: VectorRecord, *, tenant: str | None = None) -> VectorQuery:
    return VectorQuery(
        tenant_ref=tenant or record.tenant_ref,
        embedding=record.embedding,
        embedding_model=record.embedding_model,
        embedding_revision=record.embedding_revision,
        index_revision=record.index_revision,
        collection=record.collection,
        dimension=record.dimension,
        top_k=10,
    )


def _adapter(stub: QdrantStub) -> QdrantVectorStoreAdapter:
    client = httpx.Client(base_url="http://qdrant:6333", transport=httpx.MockTransport(stub))
    return QdrantVectorStoreAdapter("http://qdrant:6333", dimension=4, client=client, max_retries=0)


def test_collection_initialization_upsert_is_idempotent_and_health_works() -> None:
    stub = QdrantStub()
    adapter = _adapter(stub)
    record = _record("tenant-a")

    first = adapter.upsert((record,))
    repeated = adapter.upsert((record,))
    adapter.health()
    inventory = adapter.list_points(tenant_ref="tenant-a", embedding_revision="revision-1")

    assert len(stub.points) == 1
    assert first[0].point_id == repeated[0].point_id == record.point_id
    assert inventory[0].point_id == record.point_id
    assert inventory[0].active is True
    assert stub.points[record.point_id]["payload"]["target_type"] == "candidate_cv"
    assert stub.indexes["tenant_ref"]["data_type"] == "keyword"
    assert stub.indexes["profile_fingerprint"]["data_type"] == "keyword"
    assert stub.indexes["active"]["data_type"] == "bool"


def test_search_enforces_tenant_active_revision_and_filters() -> None:
    stub = QdrantStub()
    adapter = _adapter(stub)
    tenant_a = _record("tenant-a", source_id="cv:tenant-a")
    tenant_b = _record("tenant-b", source_id="cv:tenant-b")
    other_revision = _record("tenant-a", revision="revision-2", source_id="cv:revision-2")
    adapter.upsert((tenant_a, tenant_b, other_revision))

    hits = adapter.search(_query(tenant_a))

    assert [item.point_id for item in hits] == [tenant_a.point_id]
    assert hits[0].payload == {"safe_tag": "test"}
    search_body = json.loads(
        next(
            request.content
            for request in stub.requests
            if request.url.path.endswith("/points/search")
        )
    )
    filter_keys = {item["key"] for item in search_body["filter"]["must"] if "key" in item}
    assert {"tenant_ref", "active", "embedding_model", "embedding_revision"} <= (filter_keys)


@pytest.mark.parametrize(
    ("field", "value"),
    [("collection", "other-collection"), ("index_revision", "other-revision")],
)
def test_search_rejects_query_lineage_mismatch(field: str, value: str) -> None:
    stub = QdrantStub()
    adapter = _adapter(stub)
    record = _record("tenant-a")
    query = _query(record).model_copy(update={field: value})

    with pytest.raises(VectorContractViolation) as rejected:
        adapter.search(query)

    assert rejected.value.code == "QDRANT_LINEAGE_MISMATCH"


def test_search_recomputes_point_lineage_and_rejects_tampering() -> None:
    stub = QdrantStub()
    adapter = _adapter(stub)
    record = _record("tenant-a")
    adapter.upsert((record,))
    point = stub.points.pop(record.point_id)
    point["id"] = "tampered-point-id"
    stub.points["tampered-point-id"] = point

    with pytest.raises(VectorContractViolation) as rejected:
        adapter.search(_query(record))

    assert rejected.value.code == "QDRANT_LINEAGE_VIOLATION"


def test_deactivate_and_delete_are_tenant_scoped() -> None:
    stub = QdrantStub()
    adapter = _adapter(stub)
    tenant_a = _record("tenant-a", source_id="cv:tenant-a")
    tenant_b = _record("tenant-b", source_id="cv:tenant-b")
    adapter.upsert((tenant_a, tenant_b))

    adapter.deactivate(tenant_ref="tenant-a", point_ids=(tenant_a.point_id,))
    assert adapter.search(_query(tenant_a)) == ()
    assert adapter.search(_query(tenant_b))

    adapter.delete(tenant_ref="tenant-a", point_ids=(tenant_a.point_id,))
    assert tenant_a.point_id not in stub.points
    assert tenant_b.point_id in stub.points


def test_dimension_and_existing_schema_mismatch_fail_closed() -> None:
    stub = QdrantStub(dimension=8)
    stub.exists = True
    stub.indexes = {
        **{
            name: {"data_type": "keyword"}
            for name in (
                "tenant_ref",
                "entity_type",
                "entity_id",
                "fragment_type",
                "target_type",
                "profile_version",
                "embedding_model",
                "embedding_revision",
            )
        },
        "active": {"data_type": "bool"},
    }
    client = httpx.Client(base_url="http://qdrant:6333", transport=httpx.MockTransport(stub))

    with pytest.raises(VectorContractViolation) as rejected:
        QdrantVectorStoreAdapter(
            "http://qdrant:6333",
            dimension=4,
            client=client,
            max_retries=0,
        )

    assert rejected.value.code == "QDRANT_SCHEMA_MISMATCH"


def test_existing_payload_index_mismatch_fails_before_mutation() -> None:
    stub = QdrantStub(dimension=4)
    stub.exists = True
    stub.indexes = {"tenant_ref": {"data_type": "integer"}}
    client = httpx.Client(base_url="http://qdrant:6333", transport=httpx.MockTransport(stub))

    with pytest.raises(VectorContractViolation) as rejected:
        QdrantVectorStoreAdapter(
            "http://qdrant:6333",
            dimension=4,
            client=client,
            max_retries=0,
        )

    assert rejected.value.code == "QDRANT_SCHEMA_MISMATCH"
    assert not any(request.url.path.endswith("/index") for request in stub.requests)


def test_qdrant_unavailability_is_explicit_without_memory_fallback() -> None:
    def unavailable(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    client = httpx.Client(base_url="http://qdrant:6333", transport=httpx.MockTransport(unavailable))
    with pytest.raises(VectorContractViolation) as rejected:
        QdrantVectorStoreAdapter("http://qdrant:6333", dimension=4, client=client, max_retries=0)

    assert rejected.value.code == "QDRANT_UNAVAILABLE"


@pytest.mark.vector_integration
@pytest.mark.skipif(
    not os.getenv("MATCHING_TEST_QDRANT_URL"), reason="dedicated Qdrant is not configured"
)
def test_live_qdrant_b4_contract() -> None:
    dimension = int(os.getenv("MATCHING_TEST_QDRANT_DIMENSION", "1024"))
    adapter = QdrantVectorStoreAdapter(
        os.environ["MATCHING_TEST_QDRANT_URL"],
        api_key=os.getenv("MATCHING_TEST_QDRANT_API_KEY") or None,
        collection_name=os.getenv("MATCHING_TEST_QDRANT_COLLECTION", "matching_fragments_v1"),
        dimension=dimension,
    )
    record = _record("tenant-live", source_id="cv:live-b4", dimension=dimension)
    adapter.upsert((record,))
    assert adapter.search(_query(record))[0].point_id == record.point_id
    adapter.deactivate(tenant_ref=record.tenant_ref, point_ids=(record.point_id,))
    assert adapter.search(_query(record)) == ()
    adapter.delete(tenant_ref=record.tenant_ref, point_ids=(record.point_id,))
    adapter.close()
