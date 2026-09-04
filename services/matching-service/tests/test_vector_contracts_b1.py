from __future__ import annotations

import math
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domain.profiles import Evidence
from app.domain.vector_contracts import (
    EmbeddingRequest,
    EmbeddingResult,
    SemanticEvidence,
    SemanticFragment,
    VectorContractViolation,
    VectorQuery,
    VectorRecord,
    deterministic_point_id,
)
from app.infrastructure.fake_vector_adapters import (
    FakeEmbeddingAdapter,
    FakeVectorStoreAdapter,
)
from app.ports.vectors import EmbeddingPort, VectorStorePort


def _fragment(
    *,
    tenant_ref: str = "tenant-a",
    source_version: str = "cv.v1",
) -> SemanticFragment:
    return SemanticFragment(
        tenant_ref=tenant_ref,
        fragment_id="fragment:responsibility:1",
        source_type="cv",
        target_type="candidate_cv",
        source_id="cv:opaque-1",
        source_version=source_version,
        profile_version="a" * 64,
        fragment_type="work_experience",
        text="Built distributed transaction services",
        evidence_ref=Evidence(
            source_id="cv:evidence:1",
            quote="Built distributed transaction services",
        ),
        language="en",
        sequence=0,
        taxonomy_version="taxonomy.v1",
        graph_version="graph.v1",
    )


def _request(
    fragment: SemanticFragment,
    *,
    revision: str = "revision-1",
    dimension: int = 4,
) -> EmbeddingRequest:
    return EmbeddingRequest(
        tenant_ref=fragment.tenant_ref,
        embedding_model="fake-model",
        embedding_revision=revision,
        dimension=dimension,
        fragments=(fragment,),
    )


def _record(
    fragment: SemanticFragment,
    *,
    revision: str = "revision-1",
    active: bool | None = None,
    payload: dict | None = None,
) -> VectorRecord:
    result = FakeEmbeddingAdapter(
        model="fake-model", revision=revision, dimension=4
    ).embed(_request(fragment, revision=revision))
    return VectorRecord.build(
        fragment=fragment,
        embedding=result.vectors[0],
        embedding_model=result.embedding_model,
        embedding_revision=result.embedding_revision,
        payload=payload or {"source_id": fragment.source_id, "kind": "work_experience"},
        active=active,
    )


def _query(tenant_ref: str, vector: tuple[float, ...]) -> VectorQuery:
    return VectorQuery(
        tenant_ref=tenant_ref,
        embedding=vector,
        embedding_model="fake-model",
        embedding_revision="revision-1",
        dimension=4,
        top_k=10,
    )


def test_fake_adapters_satisfy_technology_neutral_ports() -> None:
    embedding: EmbeddingPort = FakeEmbeddingAdapter(
        model="fake-model", revision="revision-1", dimension=4
    )
    vectors: VectorStorePort = FakeVectorStoreAdapter()

    result = embedding.embed(_request(_fragment()))
    references = vectors.upsert((_record(_fragment()),))

    assert result.embedding_model == "fake-model"
    assert references[0].index_name == "fake-derived-vector-index"


def test_point_id_and_upsert_are_idempotent() -> None:
    fragment = _fragment()
    first = _record(fragment)
    repeated = _record(fragment)
    store = FakeVectorStoreAdapter()

    first_reference = store.upsert((first,))[0]
    repeated_reference = store.upsert((repeated,))[0]

    assert first.point_id == repeated.point_id
    assert first.point_id == deterministic_point_id(
        fragment,
        embedding_model="fake-model",
        embedding_revision="revision-1",
        dimension=4,
    )
    assert UUID(first.point_id)
    assert first_reference.point_id == repeated_reference.point_id
    assert len(store.search(_query("tenant-a", first.embedding))) == 1


def test_model_or_revision_changes_point_id() -> None:
    fragment = _fragment()
    old = _record(fragment, revision="revision-1")
    new = _record(fragment, revision="revision-2")

    assert old.point_id != new.point_id
    other_model_id = deterministic_point_id(
        fragment,
        embedding_model="other-model",
        embedding_revision="revision-1",
        dimension=4,
    )
    assert other_model_id != old.point_id


def test_search_is_strictly_tenant_isolated() -> None:
    tenant_a = _record(_fragment(tenant_ref="tenant-a"))
    tenant_b = _record(_fragment(tenant_ref="tenant-b"))
    store = FakeVectorStoreAdapter()
    store.upsert((tenant_a, tenant_b))

    hits = store.search(_query("tenant-a", tenant_a.embedding))

    assert [item.tenant_ref for item in hits] == ["tenant-a"]
    assert [item.point_id for item in hits] == [tenant_a.point_id]
    assert tenant_a.point_id != tenant_b.point_id


def test_inactive_records_are_never_recalled() -> None:
    fragment = _fragment()
    active = _record(fragment)
    inactive = _record(fragment, active=False)
    store = FakeVectorStoreAdapter()
    store.upsert((active,))
    store.upsert((inactive,))

    assert active.point_id == inactive.point_id
    assert store.search(_query("tenant-a", inactive.embedding)) == ()


def test_embedding_request_rejects_cross_tenant_fragments() -> None:
    with pytest.raises(ValidationError, match="request tenant"):
        EmbeddingRequest(
            tenant_ref="tenant-a",
            embedding_model="fake-model",
            embedding_revision="revision-1",
            dimension=4,
            fragments=(_fragment(tenant_ref="tenant-b"),),
        )


def test_payload_rejects_pii_and_returned_payload_is_safe() -> None:
    fragment = _fragment()
    with pytest.raises(ValidationError, match="prohibited PII"):
        _record(fragment, payload={"email": "candidate@example.com"})

    safe = _record(fragment)
    store = FakeVectorStoreAdapter()
    store.upsert((safe,))
    hit = store.search(_query("tenant-a", safe.embedding))[0]

    assert hit.payload == {"source_id": "cv:opaque-1", "kind": "work_experience"}
    assert not ({"email", "phone", "full_name", "address"} & hit.payload.keys())

    bypassed_dto_validation = safe.model_copy(
        update={"payload": {"email": "candidate@example.com"}}
    )
    with pytest.raises(VectorContractViolation) as rejected:
        store.upsert((bypassed_dto_validation,))
    assert rejected.value.code == "VECTOR_PAYLOAD_CONTAINS_PII"


@pytest.mark.parametrize("invalid", [math.nan, math.inf, -math.inf])
def test_non_finite_vectors_fail_closed(invalid: float) -> None:
    fragment = _fragment()
    with pytest.raises(ValidationError, match="NaN or infinity"):
        VectorRecord.build(
            fragment=fragment,
            embedding=(1.0, invalid),
            embedding_model="fake-model",
            embedding_revision="revision-1",
        )
    with pytest.raises(ValidationError, match="NaN or infinity"):
        _query("tenant-a", (1.0, 0.0, invalid, 0.0))


def test_embedding_result_and_query_validate_dimension() -> None:
    fragment = _fragment()
    request = _request(fragment)
    with pytest.raises(ValidationError, match="dimension"):
        EmbeddingResult(
            tenant_ref="tenant-a",
            request_id=request.request_id,
            embedding_model="fake-model",
            embedding_revision="revision-1",
            dimension=4,
            fragment_ids=(fragment.fragment_id,),
            vectors=((1.0, 0.0),),
        )
    with pytest.raises(ValidationError, match="dimension"):
        VectorQuery(
            tenant_ref="tenant-a",
            embedding=(1.0, 0.0),
            embedding_model="fake-model",
            embedding_revision="revision-1",
            dimension=4,
        )


def test_semantic_evidence_remains_reserved_and_qdrant_is_documented_as_derived() -> None:
    assert SemanticEvidence.model_fields["schema_version"].default == "semantic-evidence.v1"
    service_root = Path(__file__).parents[1]
    scoring = (service_root / "app" / "domain" / "scoring.py").read_text("utf-8")
    matching = (service_root / "app" / "domain" / "matching.py").read_text("utf-8")
    adr = (service_root / "docs" / "adr-0001-qdrant-derived-index.md").read_text(
        "utf-8"
    )

    assert "SemanticEvidence" not in scoring
    assert "SemanticEvidence" not in matching
    assert "派生索引" in adr
    assert "不是" in adr and "权威数据库" in adr
