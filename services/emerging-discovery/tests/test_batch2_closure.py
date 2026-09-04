from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.api.contracts import DiscoveryRunRequest
from app.api.mapping import discovery_command_from_api
from app.domain.values import FrozenDict
from app.infrastructure.algorithm_registry import AlgorithmRegistry
from app.infrastructure.semantic_embeddings import (
    LocalChineseSemanticEmbeddingProvider,
    SemanticProviderUnavailable,
)
from app.main import app
from tests.test_algorithm_comparison import _payload


client = TestClient(app)
HEADERS = {"Authorization": "Bearer development-emerging-discovery-token-change-me"}


class DeterministicLocalEncoder:
    def encode(self, documents, **_kwargs):
        return np.asarray(
            [
                [
                    float("python" in document.casefold()),
                    float("java" in document.casefold()),
                    float("量子" in document),
                ]
                for document in documents
            ],
            dtype=float,
        )


def _snapshots():
    payload = _payload()
    request = DiscoveryRunRequest.model_validate(payload)
    command = discovery_command_from_api(
        contract_version=request.contract_version,
        request_id=request.request_id,
        algorithm=request.algorithm,
        time_windows=[item.model_dump(mode="json") for item in request.time_windows],
        snapshots=[item.model_dump(mode="json") for item in request.snapshots],
        position_references=[
            item.model_dump(mode="json") for item in request.position_references
        ],
        config=request.config,
    )
    return command.snapshots


def test_local_semantic_provider_available_and_fused_algorithms_work():
    provider = LocalChineseSemanticEmbeddingProvider(
        encoder=DeterministicLocalEncoder()
    )
    assert provider.available is True
    vectors = provider.embed(_snapshots())
    assert len(vectors) == 5
    assert all(len(item) == 3 for item in vectors)

    registry = AlgorithmRegistry(provider)
    pure = registry.evaluate(
        "semantic_agglomerative", _snapshots(), FrozenDict()
    )
    fused = registry.evaluate(
        "semantic_fused_agglomerative", _snapshots(), FrozenDict()
    )
    assert pure.feature_name == "local-chinese-semantic-v1"
    assert fused.feature_name == "local-semantic+tfidf-svd-skill-v1"
    assert pure.cluster_count >= 1
    assert fused.cluster_count >= 1
    assert "semantic_weight" in fused.parameters


def test_unavailable_semantic_provider_is_explicit_and_baseline_remains_available():
    provider = LocalChineseSemanticEmbeddingProvider(
        model_path="Z:/definitely-not-a-local-model"
    )
    assert provider.available is False
    with pytest.raises(SemanticProviderUnavailable, match="use baseline"):
        provider.embed(_snapshots())

    semantic = _payload()
    semantic["comparison_algorithms"] = ["semantic_agglomerative"]
    response = client.post(
        "/api/v1/discovery-comparisons", json=semantic, headers=HEADERS
    )
    assert response.status_code == 503
    assert "fallback" not in response.json()["data"]

    semantic["comparison_algorithms"] = ["baseline"]
    baseline = client.post(
        "/api/v1/discovery-comparisons", json=semantic, headers=HEADERS
    )
    assert baseline.status_code == 200


def test_emergence_breakdown_and_standard_position_difference_are_queryable():
    payload = _payload()
    payload["request_id"] = "batch2-emergence-explanation"
    payload["position_references"] = [
        {
            "position_id": "standard-python",
            "graph_version_id": "graph-v1",
            "required_skills": [{"raw_skill": "Python"}],
        },
        {
            "position_id": "standard-java",
            "graph_version_id": "graph-v1",
            "required_skills": [{"raw_skill": "Java"}],
        },
    ]
    response = client.post(
        "/api/v1/discovery-runs", json=payload, headers=HEADERS
    )
    assert response.status_code == 201
    data = response.json()["data"]
    python_cluster = next(
        item
        for item in data["clusters"]
        if "comparison-jd-0" in item["representative_jd_ids"]
    )
    emergence = python_cluster["germination_assessment"]["evidence_package"][
        "emergence_index"
    ]
    assert emergence["total_score"] == python_cluster[
        "germination_assessment"
    ]["germination_score"]
    assert len(emergence["dimensions"]) == 7
    for value in emergence["dimensions"].values():
        assert 0.0 <= value["normalized_value"] <= 1.0
        assert value["weight"] >= 0.0
        assert value["contribution"] >= 0.0
        assert value["business_meaning"]
    assert emergence["semantics"] == "composite ranking index, not a probability"

    comparison = python_cluster["standard_position_comparison"]
    assert comparison["nearest_standard_position"] == "standard-python"
    assert comparison["comprehensive_similarity"] > 0
    assert "python" in comparison["shared_skills"]
    assert isinstance(comparison["new_skills"], list)
    assert comparison["shared_responsibilities"] == "unavailable"
    assert comparison["new_responsibilities"] == "unavailable"
    assert isinstance(comparison["possible_alias"], bool)
    assert comparison["possible_industry_variant"] == "unavailable"
    assert isinstance(comparison["possible_tool_stack_variant"], bool)
    assert comparison["reason"]
