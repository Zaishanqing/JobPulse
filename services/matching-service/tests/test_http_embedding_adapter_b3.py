from __future__ import annotations

import httpx
import pytest

from app.domain.profiles import Evidence
from app.domain.vector_contracts import (
    EmbeddingRequest,
    SemanticFragment,
    VectorContractViolation,
)
from app.infrastructure.http_embedding_adapter import HttpEmbeddingAdapter


def _request() -> EmbeddingRequest:
    fragment = SemanticFragment(
        tenant_ref="tenant-a",
        fragment_id="fragment:skill:1",
        source_type="cv",
        target_type="candidate_cv",
        source_id="cv:opaque-1",
        source_version="cv.v1",
        source_profile_id="a" * 64,
        fragment_type="skill_context",
        normalized_text="Python services",
        evidence_ref=Evidence(source_id="cv:evidence:1", quote="Python services"),
        language="en",
        sequence=0,
        taxonomy_version="taxonomy.v1",
    )
    return EmbeddingRequest(
        tenant_ref="tenant-a",
        embedding_model="BAAI/bge-m3",
        embedding_revision="commit-1",
        dimension=4,
        fragments=(fragment,),
    )


def test_http_embedding_adapter_maps_service_contract(monkeypatch) -> None:
    def post(*args, **kwargs):
        request = httpx.Request("POST", args[0])
        return httpx.Response(
            200,
            request=request,
            json={
                "vectors": [[1.0, 0.0, 0.0, 0.0]],
                "model_id": "BAAI/bge-m3",
                "model_revision": "commit-1",
                "dimension": 4,
                "normalized": True,
                "usage": {"input_count": 1, "character_count": 15},
                "latency_ms": 1.5,
            },
        )

    monkeypatch.setattr(httpx, "post", post)
    result = HttpEmbeddingAdapter(
        "http://embedding:8000",
        model="BAAI/bge-m3",
        revision="commit-1",
        dimension=4,
    ).embed(_request())

    assert result.vectors == ((1.0, 0.0, 0.0, 0.0),)


def test_http_embedding_adapter_fails_closed_without_fallback(monkeypatch) -> None:
    def post(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "post", post)
    adapter = HttpEmbeddingAdapter(
        "http://embedding:8000",
        model="BAAI/bge-m3",
        revision="commit-1",
        dimension=4,
    )

    with pytest.raises(VectorContractViolation) as rejected:
        adapter.embed(_request())

    assert rejected.value.code == "EMBEDDING_UNAVAILABLE"


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("model_id", "other-model", "EMBEDDING_MODEL_ID_MISMATCH"),
        ("model_revision", "other-revision", "EMBEDDING_REVISION_MISMATCH"),
        ("dimension", 8, "EMBEDDING_DIMENSION_MISMATCH"),
        ("normalized", False, "EMBEDDING_NORMALIZATION_MISMATCH"),
        ("representation", "sparse", "EMBEDDING_REPRESENTATION_MISMATCH"),
    ],
)
def test_http_embedding_adapter_startup_contract_rejects_mismatch(
    monkeypatch, field, value, code
) -> None:
    model = {
        "model_id": "BAAI/bge-m3",
        "model_revision": "commit-1",
        "dimension": 4,
        "representation": "dense",
        "similarity": "cosine",
        "normalized": True,
        "normalization": "l2",
        "device": "cpu",
        "use_fp16": False,
    }
    model[field] = value

    def get(*args, **kwargs):
        return httpx.Response(200, request=httpx.Request("GET", args[0]), json=model)

    monkeypatch.setattr(httpx, "get", get)
    adapter = HttpEmbeddingAdapter(
        "http://embedding:8000",
        model="BAAI/bge-m3",
        revision="commit-1",
        dimension=4,
    )
    with pytest.raises(VectorContractViolation) as rejected:
        adapter.check_startup_contract()
    assert rejected.value.code == code


def test_http_embedding_adapter_accepts_runtime_placement_metadata(monkeypatch) -> None:
    model = {
        "model_id": "BAAI/bge-m3",
        "model_revision": "commit-1",
        "dimension": 4,
        "representation": "dense",
        "similarity": "cosine",
        "normalized": True,
        "normalization": "l2",
        "device": "cpu",
        "use_fp16": False,
    }

    def get(*args, **kwargs):
        return httpx.Response(200, request=httpx.Request("GET", args[0]), json=model)

    monkeypatch.setattr(httpx, "get", get)
    HttpEmbeddingAdapter(
        "http://embedding:8000",
        model="BAAI/bge-m3",
        revision="commit-1",
        dimension=4,
    ).check_startup_contract()
