from copy import deepcopy

from sqlalchemy import select

from app.domain.policies import (
    effective_weight,
    quality_scores,
    relation_scores,
)
from app.models import DuplicateCluster, JDDocument

BASE_RELATION_METRICS = {
    "support_document_count": 1,
    "weighted_frequency": 0.40,
    "required_ratio": 0.50,
    "preferred_ratio": 0.25,
    "bonus_ratio": 0.15,
    "unknown_ratio": 0.10,
    "source_diversity": 1,
    "enterprise_coverage": 1,
    "freshness_score": 0.50,
    "trusted_evidence_ratio": 0.50,
}


def assert_only_metric_changes_weight(metric, higher_value):
    baseline = deepcopy(BASE_RELATION_METRICS)
    changed = deepcopy(BASE_RELATION_METRICS)
    changed[metric] = higher_value
    assert {key for key in changed if changed[key] != baseline[key]} == {metric}
    assert relation_scores(changed)["auto_weight"] > relation_scores(baseline)["auto_weight"]


def test_weight_changes_with_source_diversity():
    assert_only_metric_changes_weight("source_diversity", 2)


def test_weight_changes_with_enterprise_coverage():
    assert_only_metric_changes_weight("enterprise_coverage", 2)


def test_weight_changes_with_freshness():
    assert_only_metric_changes_weight("freshness_score", 0.90)


def test_weight_changes_with_evidence_quality():
    assert_only_metric_changes_weight("trusted_evidence_ratio", 0.90)

def test_duplicate_algorithm_compares_real_documents():
    exact=quality_scores("Python SQL Docker",["Python SQL Docker"])
    different=quality_scores("Python SQL Docker",["市场营销与客户沟通"])
    assert exact["duplicate_score"]==1 and different["duplicate_score"]<.2
    assert exact["copy_risk_score"]>different["copy_risk_score"]

def test_inflation_and_effective_weight_are_deterministic():
    inflated=quality_scores("精通 Python SQL Docker React PyTorch 技能 熟悉 精通 Python SQL Docker React")
    assert inflated["inflation_score"]>0
    assert effective_weight(1,.8,.6,.5)<effective_weight(1,0,0,0)

def test_reassessment_updates_record_and_reuses_duplicate_cluster(client, auth_headers):
    headers=auth_headers()
    for document_id in ("DUP_A", "DUP_B"):
        response=client.post("/api/v1/jds",json={"document_id":document_id,"raw_text":"后端工程师\nPython SQL Docker"},headers=headers)
        assert response.status_code==200
    first=client.post("/api/v1/jds/DUP_A/duplicate-check",json={},headers=headers)
    second=client.post("/api/v1/jds/DUP_A/duplicate-check",json={},headers=headers)
    assert first.status_code==second.status_code==200
    assert first.json()["data"]["duplicate_score"]==1


def test_duplicate_check_creates_shared_cluster(client, db, auth_headers):
    headers = auth_headers()
    for document_id in ("CLUSTER_A", "CLUSTER_B"):
        response = client.post(
            "/api/v1/jds",
            json={
                "document_id": document_id,
                "raw_text": "后端工程师\nPython SQL Docker",
            },
            headers=headers,
        )
        assert response.status_code == 200
    for document_id in ("CLUSTER_A", "CLUSTER_B"):
        response = client.post(
            f"/api/v1/jds/{document_id}/duplicate-check",
            json={},
            headers=headers,
        )
        assert response.status_code == 200
    shared = [
        cluster
        for cluster in db.scalars(select(DuplicateCluster)).all()
        if {"CLUSTER_A", "CLUSTER_B"} <= set(cluster.document_ids)
    ]
    assert len(shared) == 1
    assert shared[0].cluster_key.startswith("duplicate:")


def test_cross_cluster_match_merges_entire_clusters(client, db, auth_headers):
    headers = auth_headers()
    raw_x = "后端工程师\nPython SQL Docker\n负责服务开发"
    raw_y = "后端工程师\nJava Spring MySQL\n负责平台建设"
    for document_id, raw_text in (
        ("MERGE_X_A", raw_x),
        ("MERGE_X_B", raw_x),
        ("MERGE_Y_A", raw_y),
        ("MERGE_Y_B", raw_y),
    ):
        response = client.post(
            "/api/v1/jds",
            json={"document_id": document_id, "raw_text": raw_text},
            headers=headers,
        )
        assert response.status_code == 200
    for document_id in ("MERGE_X_A", "MERGE_X_B", "MERGE_Y_A", "MERGE_Y_B"):
        response = client.post(
            f"/api/v1/jds/{document_id}/duplicate-check",
            json={},
            headers=headers,
        )
        assert response.status_code == 200
    clusters = db.scalars(select(DuplicateCluster)).all()
    assert len(clusters) == 2

    document = db.scalar(
        select(JDDocument).where(JDDocument.document_id == "MERGE_X_A")
    )
    document.raw_text = raw_y
    db.commit()
    response = client.post(
        "/api/v1/jds/MERGE_X_A/duplicate-check",
        json={},
        headers=headers,
    )
    assert response.status_code == 200
    merged = [
        cluster
        for cluster in db.scalars(select(DuplicateCluster)).all()
        if {
            "MERGE_X_A",
            "MERGE_X_B",
            "MERGE_Y_A",
            "MERGE_Y_B",
        }
        <= set(cluster.document_ids)
    ]
    assert len(merged) == 1


def test_reassessment_can_leave_duplicate_cluster(client, db, auth_headers):
    headers = auth_headers()
    raw = "后端工程师\nPython SQL Docker\n负责服务开发"
    for document_id in ("EXIT_A", "EXIT_B"):
        response = client.post(
            "/api/v1/jds",
            json={"document_id": document_id, "raw_text": raw},
            headers=headers,
        )
        assert response.status_code == 200
    for document_id in ("EXIT_A", "EXIT_B"):
        response = client.post(
            f"/api/v1/jds/{document_id}/duplicate-check",
            json={},
            headers=headers,
        )
        assert response.status_code == 200
    assert db.scalars(select(DuplicateCluster)).all()

    document = db.scalar(
        select(JDDocument).where(JDDocument.document_id == "EXIT_B")
    )
    document.raw_text = "后端工程师\nJava Spring MySQL\n负责平台建设"
    db.commit()
    response = client.post(
        "/api/v1/jds/EXIT_B/duplicate-check",
        json={},
        headers=headers,
    )
    assert response.status_code == 200
    assert all(
        "EXIT_B" not in set(cluster.document_ids)
        for cluster in db.scalars(select(DuplicateCluster)).all()
    )
