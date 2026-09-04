from __future__ import annotations

import hashlib
from fastapi.testclient import TestClient

from app.infrastructure.algorithm_registry import AlgorithmRegistry
from app.main import app


client = TestClient(app)
HEADERS = {"Authorization": "Bearer development-emerging-discovery-token-change-me"}


def _snapshot(index: int, title: str, skill: str) -> dict:
    return {
        "source_fact_id": f"comparison-fact-{index}",
        "source_fact_version": "1",
        "jd_id": f"comparison-jd-{index}",
        "schema_version": "v2",
        "review_status": "published",
        "consumption_path": "published",
        "title": title,
        "source_name": f"platform-{index % 2}",
        "publish_date": f"2026-0{index + 1}-01",
        "content_hash": "sha256:" + hashlib.sha256(
            f"comparison:{index}".encode()
        ).hexdigest(),
        "structured_data": {
            "responsibilities": [title],
            "required_skills": [{"raw_skill": skill}],
            "bonus_skills": [],
            "industry": "软件",
            "business_scenarios": [title],
            "source_record_id": f"comparison-source-{index}",
        },
    }


def _payload() -> dict:
    return {
        "contract_version": "discovery.v2",
        "request_id": "algorithm-comparison",
        "algorithm": "emerge_v3_2",
        "time_windows": [
            {"window_id": "h1", "start": "2026-01-01", "end": "2026-02-28"},
            {"window_id": "h2", "start": "2026-03-01", "end": "2026-04-30"},
            {"window_id": "h3", "start": "2026-05-01", "end": "2026-06-30"},
        ],
        "snapshots": [
            _snapshot(0, "Python 数据工程师", "Python"),
            _snapshot(1, "Python 数据工程师", "Python"),
            _snapshot(2, "Java 后端工程师", "Java"),
            _snapshot(3, "Java 后端工程师", "Java"),
            _snapshot(4, "量子生物研究员", "CRISPR"),
        ],
        "position_references": [
            {
                "position_id": "formal-reference",
                "graph_version_id": "graph-v1",
                "required_skills": [{"raw_skill": "SQL"}],
            }
        ],
        "config": {"dataset_id": "emerging-discovery-full-temporal-v1"},
    }


def test_registry_declares_features_clustering_and_parameters():
    registry = AlgorithmRegistry()
    assert registry.names() == (
        "baseline",
        "multi_view",
        "fused_agglomerative",
        "density_noise",
        "semantic_agglomerative",
        "semantic_fused_agglomerative",
    )
    profiles = {item.name: item for item in registry.profiles()}
    assert profiles["baseline"].feature_name.startswith("tfidf-svd-v1")
    assert profiles["baseline"].defaults["text_weight"] == 1.0
    assert profiles["baseline"].clustering_name == "agglomerative-average-link"
    assert profiles["baseline"].defaults["similarity_threshold"] == 0.55
    assert profiles["density_noise"].clustering_name == "cosine-density-dbscan"
    assert profiles["density_noise"].defaults["min_samples"] == 2
    assert profiles["multi_view"].clustering_name == "evidence-gated-multi-view"


def test_formal_discovery_preserves_the_frozen_occupation_groups():
    payload = _payload()
    payload["comparison_algorithms"] = ["baseline"]
    compared = client.post(
        "/api/v1/discovery-comparisons", json=payload, headers=HEADERS
    )
    assert compared.status_code == 200
    baseline_groups = sorted(
        sorted(item["member_jd_ids"])
        for item in compared.json()["data"]["algorithms"][0]["clusters"]
    )

    payload.pop("comparison_algorithms")
    payload["request_id"] = "algorithm-baseline-single"
    payload["algorithm"] = "emerge_v3_2"
    single = client.post("/api/v1/discovery-runs", json=payload, headers=HEADERS)
    assert single.status_code == 201
    single_groups = sorted(
        sorted(item["representative_jd_ids"])
        for item in single.json()["data"]["clusters"]
    )
    assert single_groups == baseline_groups
    assert ["comparison-jd-4"] in baseline_groups
    assert ["comparison-jd-4"] in single_groups


def test_multi_algorithm_metrics_and_density_noise_points_are_explicit():
    payload = _payload()
    payload["comparison_algorithms"] = [
        "baseline",
        "fused_agglomerative",
        "density_noise",
    ]
    payload["algorithm_configs"] = {
        "density_noise": {"eps": 0.2, "min_samples": 2}
    }
    response = client.post(
        "/api/v1/discovery-comparisons", json=payload, headers=HEADERS
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["input_quality_report"]["deduplicated_jd_count"] == 5
    results = {item["algorithm"]: item for item in data["algorithms"]}
    assert set(results) == {
        "baseline",
        "fused_agglomerative",
        "density_noise",
    }
    for item in results.values():
        assert item["cluster_count"] >= 0
        assert 0.0 <= item["noise_ratio"] <= 1.0
        assert item["runtime_ms"] >= 0.0
        assert {
            "silhouette_coefficient",
            "intra_cluster_similarity",
            "inter_cluster_difference",
        } <= set(item)
        assert item["enterprise_debias"]["status"] == "unavailable"
        assert 0.0 <= item["stability_analysis"]["stability_score"] <= 1.0
        assert 0.0 <= item["recommendation_score"] <= 1.0
    density = results["density_noise"]
    assert density["cluster_count"] == 2
    assert density["noise_ratio"] == 0.2
    assert [item["jd_id"] for item in density["noise_points"]] == [
        "comparison-jd-4"
    ]
    assert density["silhouette_coefficient"] is not None
    assert density["intra_cluster_similarity"] is not None
    assert density["inter_cluster_difference"] is not None
    assert data["recommended_algorithm"] in results
    assert data["recommendation_reason"]
    assert results[data["recommended_algorithm"]]["recommendation_score"] == max(
        item["recommendation_score"] for item in results.values()
    )


def test_enterprise_debias_head_removal_and_parameter_sensitivity():
    payload = _payload()
    enterprises = ("company-a", "company-a", "company-a", "company-b", "company-c")
    for snapshot, enterprise in zip(payload["snapshots"], enterprises, strict=True):
        snapshot["structured_data"]["enterprise_id"] = enterprise
    payload["comparison_algorithms"] = ["baseline", "density_noise"]
    payload["algorithm_configs"] = {
        "baseline": {"enterprise_max_sample_ratio": 0.4},
        "density_noise": {
            "eps": 0.2,
            "min_samples": 2,
            "enterprise_max_sample_ratio": 0.4,
        },
    }
    response = client.post(
        "/api/v1/discovery-comparisons", json=payload, headers=HEADERS
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["input_quality_report"]["enterprise_count"] == 3
    results = {item["algorithm"]: item for item in data["algorithms"]}
    for item in results.values():
        debias = item["enterprise_debias"]
        assert debias["status"] == "applied"
        assert debias["top_enterprise"] == "company-a"
        assert debias["top_enterprise_share_before"] == 0.6
        assert debias["top_enterprise_share_after"] <= 0.5
        assert debias["concentration_after"] < debias["concentration_before"]
        assert debias["similar_group_count"] >= 1
        assert debias["sample_weights"]["comparison-jd-0"] < 1.0
        assert debias["without_top_enterprise"]["remaining_sample_count"] == 2
        assert debias["without_top_enterprise"]["member_consistency"] is not None

    baseline_parameters = {
        item["parameter"] for item in results["baseline"]["parameter_sensitivity"]
    }
    assert baseline_parameters == {
        "text_weight",
        "enterprise_max_sample_ratio",
        "similarity_threshold",
    }
    density_parameters = {
        item["parameter"]
        for item in results["density_noise"]["parameter_sensitivity"]
    }
    assert density_parameters == {
        "text_weight",
        "enterprise_max_sample_ratio",
        "eps",
        "min_samples",
    }
    for item in results.values():
        stability = item["stability_analysis"]
        assert stability["method"] == "deterministic-parameter-perturbation-v1"
        assert stability["run_count"] >= 5
        assert stability["cluster_count_min"] <= stability["cluster_count_max"]
        assert 0.0 <= stability["member_consistency"] <= 1.0


def test_small_sample_metrics_are_unavailable_instead_of_failing():
    payload = _payload()
    payload["request_id"] = "algorithm-small-sample"
    payload["snapshots"] = payload["snapshots"][:1]
    payload["comparison_algorithms"] = ["baseline", "density_noise"]
    response = client.post(
        "/api/v1/discovery-comparisons", json=payload, headers=HEADERS
    )
    assert response.status_code == 200
    results = {item["algorithm"]: item for item in response.json()["data"]["algorithms"]}
    assert results["baseline"]["cluster_count"] == 1
    assert results["baseline"]["silhouette_coefficient"] is None
    assert results["baseline"]["intra_cluster_similarity"] is None
    assert results["baseline"]["inter_cluster_difference"] is None
    assert results["density_noise"]["cluster_count"] == 0
    assert results["density_noise"]["noise_ratio"] == 1.0
    assert len(results["density_noise"]["noise_points"]) == 1


def test_unknown_comparison_algorithm_and_parameter_are_stable_422():
    unknown = _payload()
    unknown["comparison_algorithms"] = ["not-registered"]
    assert client.post(
        "/api/v1/discovery-comparisons", json=unknown, headers=HEADERS
    ).status_code == 422

    invalid_parameter = _payload()
    invalid_parameter["comparison_algorithms"] = ["density_noise"]
    invalid_parameter["algorithm_configs"] = {
        "density_noise": {"unknown_parameter": 1}
    }
    response = client.post(
        "/api/v1/discovery-comparisons",
        json=invalid_parameter,
        headers=HEADERS,
    )
    assert response.status_code == 422
    assert "unsupported parameter" in response.json()["message"]
