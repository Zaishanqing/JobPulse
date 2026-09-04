from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.runtime_database import reset_database_data, Base, SessionLocal, engine
from app.main import app
from app.models.emerging_position import EmergingPosition
from app.models.position_cluster import PositionCluster
from app.models.standard_position import StandardPosition
from tests.user_factory import create_internal_user

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_database():
    reset_database_data()
    yield
    reset_database_data()


def headers(username: str) -> dict[str, str]:
    create_internal_user(username, "admin")
    client.post(
        "/api/v1/auth/register",
        json={
            "role": "admin",
            "username": username,
            "password": "password123",
            "email": f"{username}@example.com",
            "phone": "13800000000",
        },
    )
    token = client.post(
        "/api/v1/auth/login", json={"username": username, "password": "password123"}
    ).json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def candidate(auth: dict[str, str], *, valid_run: bool) -> str:
    evidence = {
        "source_jd_id": "jd-1",
        "original_text_snippet": "负责 RAG 应用交付",
        "field_type": "responsibility",
        "data_source": "platform-a",
        "window_id": "w1",
        "locator": {
            "source_fact_id": "fact-1",
            "source_fact_version": "1",
            "structured_path": "$.responsibilities[0]",
        },
    }
    dimensions = {
        name: {"normalized_value": 0.8, "weight": weight, "contribution": 0.8 * weight}
        for name, weight in {
            "growth": 0.18,
            "cross_window_persistence": 0.16,
            "enterprise_coverage": 0.12,
            "source_diversity": 0.12,
            "standard_position_distance": 0.18,
            "evidence_quality": 0.12,
            "result_stability": 0.12,
        }.items()
    }
    field_evidence = {
        "position_summary": {"content": "负责 RAG 应用交付"},
        "core_responsibilities": {"content": ["负责 RAG 应用交付"], "items": [{"content": "负责 RAG 应用交付", "evidence": [evidence]}]},
        "required_skills": {"content": [{"raw_skill": "skill-x"}], "items": [{"content": "skill-x", "evidence": [evidence]}]},
        "distinguishing_features": {"content": ["RAG 工作流"], "items": [{"content": "RAG 工作流", "evidence": [evidence]}]},
        "representative_enterprises": {"content": {"企业甲": 3}},
        "growth_trajectory": {"content": [{"window_id": value, "member_count": 1} for value in ("w1", "w2", "w3")]},
        "industry_scenarios": {"content": ["企业知识库 RAG 应用"], "items": [{"content": "企业知识库 RAG 应用", "evidence": [evidence]}]},
    }
    with SessionLocal() as db:
        cluster = PositionCluster(
            cluster_name="向量技能组合岗位簇",
            algorithm="tfidf-svd-skill-agglomerative-v1",
            sample_count=3,
            core_skills=[{"raw_skill": "skill-x"}],
            representative_titles=["岗位"],
            representative_jd_ids=["jd-1", "jd-2", "jd-3"],
            stability_score=0.9,
            growth_score=0.7,
            distance_from_existing_positions=0.8,
            discovery_run_id="run-1" if valid_run else None,
            discovery_run_status="succeeded" if valid_run else None,
            discovery_assessment=(
                    {
                        "germination_score": 0.8,
                        "qualified_as_emerging": True,
                        "evidence_package": {"emergence_index": {"dimensions": dimensions}},
                    }
                if valid_run
                else {}
            ),
            generated_definition={
                "position_name": "向量技能岗位",
                "position_summary": "负责 RAG 应用交付",
                "core_responsibilities": ["负责 RAG 应用交付"],
                "required_skills": [{"raw_skill": "skill-x"}],
                "industry_scenarios": ["企业知识库 RAG 应用"],
                "distinguishing_features": ["RAG 工作流"],
                "representative_enterprises": {"企业甲": 3},
                "growth_trajectory": field_evidence["growth_trajectory"]["content"],
                "field_evidence": field_evidence,
            },
            status="active",
        )
        db.add(cluster)
        db.commit()
        cluster_id = cluster.id
    response = client.post(
        f"/api/v1/emerging-positions/from-cluster/{cluster_id}", headers=auth
    )
    assert response.status_code == 200
    return response.json()["data"]["emerging_id"]


def approve(auth: dict[str, str], emerging_id: str) -> None:
    assert client.post(
        f"/api/v1/emerging-positions/{emerging_id}/submit-review", headers=auth
    ).status_code == 200
    assert client.post(
        f"/api/v1/emerging-positions/{emerging_id}/review",
        json={"conclusion": "approved", "reason": "Evidence 完整"},
        headers=auth,
    ).status_code == 200


def test_candidate_cannot_publish_or_promote_without_human_review():
    auth = headers("gate_unreviewed")
    emerging_id = candidate(auth, valid_run=True)
    assert client.post(
        f"/api/v1/emerging-positions/{emerging_id}/publish", headers=auth
    ).status_code == 409
    assert client.post(
        f"/api/v1/emerging-positions/{emerging_id}/promote-to-position", headers=auth
    ).status_code == 409
    with SessionLocal() as db:
        assert db.query(StandardPosition).count() == 0


def test_publish_requires_successful_remote_algorithm_evidence():
    auth = headers("gate_missing_run")
    emerging_id = candidate(auth, valid_run=False)
    approve(auth, emerging_id)
    assert client.post(
        f"/api/v1/emerging-positions/{emerging_id}/publish", headers=auth
    ).status_code == 409


def test_review_publish_and_promote_follow_transactional_gate_order():
    auth = headers("gate_valid")
    emerging_id = candidate(auth, valid_run=True)
    approve(auth, emerging_id)
    published = client.post(
        f"/api/v1/emerging-positions/{emerging_id}/publish", headers=auth
    )
    assert published.status_code == 200
    promoted = client.post(
        f"/api/v1/emerging-positions/{emerging_id}/promote-to-position", headers=auth
    )
    assert promoted.status_code == 200
    with SessionLocal() as db:
        assert db.query(StandardPosition).count() == 1


def test_candidate_edit_rejects_algorithm_score_and_evidence_fields():
    auth = headers("gate_edit")
    emerging_id = candidate(auth, valid_run=True)
    response = client.put(
        f"/api/v1/emerging-positions/{emerging_id}",
        json={
            "position_name": "人工编辑名称",
            "germination_score": 0.0,
            "score_dimensions": {"fake": 1},
            "evidence_jd_ids": [],
        },
        headers=auth,
    )
    assert response.status_code == 422
    changed = client.get(
        f"/api/v1/emerging-positions/{emerging_id}", headers=auth
    ).json()["data"]
    assert changed["evidence_jd_ids"] == ["jd-1", "jd-2", "jd-3"]
    assert changed["germination_score"] == 0.8
    assert "fake" not in changed["score_dimensions"]


def test_publish_and_promotion_commit_failures_leave_no_half_state(monkeypatch):
    from sqlalchemy.orm import Session

    auth = headers("gate_atomic")
    emerging_id = candidate(auth, valid_run=True)
    approve(auth, emerging_id)
    original_commit = Session.commit

    with monkeypatch.context() as scoped:
        scoped.setattr(Session, "commit", lambda self: (_ for _ in ()).throw(RuntimeError("publish commit failure")))
        with pytest.raises(RuntimeError, match="publish commit failure"):
            client.post(
                f"/api/v1/emerging-positions/{emerging_id}/publish", headers=auth
            )
    with SessionLocal() as db:
        assert db.get(EmergingPosition, emerging_id).status == "approved"

    assert client.post(
        f"/api/v1/emerging-positions/{emerging_id}/publish", headers=auth
    ).status_code == 200
    with monkeypatch.context() as scoped:
        scoped.setattr(Session, "commit", lambda self: (_ for _ in ()).throw(RuntimeError("promotion commit failure")))
        with pytest.raises(RuntimeError, match="promotion commit failure"):
            client.post(
                f"/api/v1/emerging-positions/{emerging_id}/promote-to-position",
                headers=auth,
            )
    with SessionLocal() as db:
        assert db.query(StandardPosition).count() == 0
        assert db.get(EmergingPosition, emerging_id).status == "published"
    assert original_commit is Session.commit
