from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.contexts.market_intelligence import ManagePredictedPositions
from app.contexts.market_intelligence._applications.trends import ManagePredictedPositions as ManagePredictedPositionsApplication
from app.contexts.market_intelligence._ports.trends import (
    PositionComparisonProfile,
    PredictedPositionRecord,
)
from app.main import app
from app.models.emerging_position import EmergingPosition
from app.models.position_cluster import PositionCluster
from app.models.predicted_position import PredictedPosition
from app.models.predicted_position_workflow import (
    PredictedPositionDefinitionVersion,
    PredictedPositionMatch,
    PredictedPositionRelationVersion,
)
from app.models.skill import Skill
from app.models.skill_normalization_candidate import SkillNormalizationCandidate
from app.models.standard_position import StandardPosition
from app.infrastructure.trends import SqlAlchemyPredictedPositionRepository
from tests.runtime_database import reset_database_data, Base, SessionLocal, engine
from tests.user_factory import create_internal_user


client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_database():
    reset_database_data()
    yield
    reset_database_data()


def _token(username: str, role: str = "developer") -> str:
    create_internal_user(username, role)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "password123"},
    )
    return response.json()["data"]["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _prediction(token: str) -> tuple[str, str]:
    response = client.post(
        "/api/v1/predicted-positions/tasks",
        json={},
        headers=_headers(token),
    )
    assert response.status_code == 200
    data = response.json()["data"]
    return data["predicted_ids"][0], data["provider_run_id"]


def _comparison_targets(predicted_id: str) -> tuple[int, int]:
    with SessionLocal() as db:
        skill = Skill(skill_name="大模型", category="人工智能")
        db.add(skill)
        db.flush()
        skill_value = {"skill_id": skill.id, "skill_name": skill.skill_name}
        standard = StandardPosition(
            position_name="AI大模型训练师",
            core_responsibilities=["训练和评估大模型"],
            required_skills=[skill_value],
            bonus_skills=[],
            industry_scenarios=["人工智能/互联网"],
        )
        cluster = PositionCluster(cluster_name="大模型岗位簇", algorithm="test")
        db.add_all([standard, cluster])
        db.flush()
        emerging = EmergingPosition(
            cluster_id=cluster.id,
            position_name="大模型应用训练师",
            core_responsibilities=["训练和评估大模型"],
            required_skills=[skill_value],
            bonus_skills=[],
            industry_scenarios=["人工智能/互联网"],
            evidence_jd_ids=["evidence-jd-1"],
            status="published",
        )
        historical = PredictedPosition(
            position_name="AI大模型训练师",
            provider_run_id="historical-run",
            candidate_key="historical-candidate",
            potential_responsibilities=["训练和评估大模型"],
            potential_skills=["大模型"],
            industry_scenarios=["人工智能/互联网"],
            evidence_references=["trend-intelligence:snapshot:test-snapshot-1"],
            confidence_score=0.7,
        )
        current = db.get(PredictedPosition, predicted_id)
        current.potential_responsibilities = ["训练和评估大模型"]
        current.potential_skills = ["大模型", "尚未归一化技能"]
        db.add_all([emerging, historical])
        db.commit()
        return db.query(StandardPosition).count(), db.query(EmergingPosition).count()


def test_matching_definition_review_publish_and_relation_closed_loop() -> None:
    token = _token("prediction_workflow_developer")
    predicted_id, _ = _prediction(token)
    original_counts = _comparison_targets(predicted_id)

    first_match = client.post(
        f"/api/v1/predicted-positions/{predicted_id}/matches/tasks",
        headers=_headers(token),
    )
    second_match = client.post(
        f"/api/v1/predicted-positions/{predicted_id}/matches/tasks",
        headers=_headers(token),
    )
    assert first_match.status_code == 200
    first_results = first_match.json()["data"]["results"]
    assert {item["target_type"] for item in first_results} == {
        "standard_position", "emerging_position", "predicted_position"
    }
    assert any(item["recommendation"] == "possible_duplicate" for item in first_results)
    second_results = second_match.json()["data"]["results"]
    assert {item["match_id"] for item in second_results} == {
        item["match_id"] for item in first_results
    }
    assert {item["version"] for item in second_results} == {1}
    assert all(
        {"name", "skills", "responsibilities", "industry_scenarios", "trend_evidence"}
        <= set(item["overlap_evidence"])
        for item in first_results
    )

    generated = client.post(
        f"/api/v1/predicted-positions/{predicted_id}/definition-drafts",
        headers=_headers(token),
    ).json()["data"]
    repeated = client.post(
        f"/api/v1/predicted-positions/{predicted_id}/definition-drafts",
        headers=_headers(token),
    ).json()["data"]
    assert repeated["definition_id"] == generated["definition_id"]
    assert any(
        item["resolution_status"] == "unresolved"
        and item["skill_id"] is None
        for item in generated["definition"]["required_skills"]
    )

    evidence = ["trend-intelligence:snapshot:test-snapshot-1"]
    edited_response = client.put(
        f"/api/v1/predicted-positions/{predicted_id}/definition-drafts/{generated['definition_id']}",
        headers=_headers(token),
        json={
            "core_responsibilities": ["训练和评估大模型"],
            "required_skills": ["大模型"],
            "industry_scenarios": ["人工智能/互联网"],
            "formation_basis": [{"type": "multi_source_trend", "evidence": evidence}],
            "evidence_by_conclusion": {
                "position_name": evidence,
                "core_responsibilities": evidence,
                "required_skills": {"大模型": evidence},
                "industry_scenarios": evidence,
                "formation_basis": evidence,
            },
        },
    )
    assert edited_response.status_code == 200
    edited = edited_response.json()["data"]
    assert edited["version"] == generated["version"] + 1
    assert edited["definition"]["required_skills"][0]["skill_id"]

    submitted = client.post(
        f"/api/v1/predicted-positions/{predicted_id}/definition-drafts/{edited['definition_id']}/submit-review",
        headers=_headers(token),
        json={"reason": "多源证据齐全"},
    ).json()["data"]
    review_id = submitted["review_task_id"]
    assert client.post(f"/api/v1/review-tasks/{review_id}/claim", headers=_headers(token)).status_code == 200
    assert client.post(f"/api/v1/review-tasks/{review_id}/approve", headers=_headers(token)).status_code == 200
    history = client.get(f"/api/v1/review-tasks/{review_id}/history", headers=_headers(token))
    assert [item["action"] for item in history.json()["data"]] == ["create", "claim", "approve"]

    relation = client.post(
        f"/api/v1/predicted-positions/{predicted_id}/relations",
        headers=_headers(token),
        json={"relation_type": "independent", "reason": "独立趋势岗位"},
    ).json()["data"]
    original_relation = relation
    updated = client.put(
        f"/api/v1/predicted-positions/{predicted_id}/relations/{relation['relation_id']}",
        headers=_headers(token),
        json={"relation_type": "independent", "reason": "更新后趋势岗位"},
    ).json()["data"]
    assert updated["version"] == relation["version"] + 1
    assert updated["relation_identity_id"] == relation["relation_identity_id"]
    current_after_update = client.get(
        f"/api/v1/predicted-positions/{predicted_id}/relations",
        headers=_headers(token),
    ).json()["data"]
    assert [item["relation_id"] for item in current_after_update] == [
        updated["relation_id"]
    ]
    relation = updated
    deleted = client.delete(
        f"/api/v1/predicted-positions/{predicted_id}/relations/{relation['relation_id']}",
        headers=_headers(token),
    ).json()["data"]
    assert deleted["status"] == "deleted"
    assert deleted["version"] == relation["version"] + 1
    assert deleted["relation_identity_id"] == relation["relation_identity_id"]
    assert deleted["supersedes_relation_id"] == relation["relation_id"]
    current_relations = client.get(
        f"/api/v1/predicted-positions/{predicted_id}/relations",
        headers=_headers(token),
    ).json()["data"]
    assert current_relations == []
    history = client.get(
        f"/api/v1/predicted-positions/{predicted_id}/relations/history",
        headers=_headers(token),
    ).json()["data"]
    assert [item["relation_id"] for item in history] == [
        deleted["relation_id"],
        updated["relation_id"],
        original_relation["relation_id"],
    ]

    published = client.post(
        f"/api/v1/predicted-positions/{predicted_id}/publish",
        headers=_headers(token),
        json={"definition_id": edited["definition_id"]},
    )
    assert published.status_code == 200
    assert published.json()["data"]["status"] == "published"
    immutable = client.put(
        f"/api/v1/predicted-positions/{predicted_id}/definition-drafts/{edited['definition_id']}",
        headers=_headers(token),
        json={"position_name": "不得覆盖已发布版本"},
    )
    assert immutable.status_code == 422
    personal = _token("prediction_workflow_reader", "personal_user")
    assert client.get(
        f"/api/v1/predicted-positions/{predicted_id}", headers=_headers(personal)
    ).status_code == 200
    assert client.post(
        f"/api/v1/predicted-positions/{predicted_id}/definition-drafts",
        headers=_headers(personal),
    ).status_code == 403
    with SessionLocal() as db:
        assert db.query(StandardPosition).count() == original_counts[0]
        assert db.query(EmergingPosition).count() == original_counts[1]
        assert db.query(PredictedPositionMatch).count() == 3
        assert db.query(PredictedPositionDefinitionVersion).count() == 2
        assert db.query(PredictedPositionRelationVersion).count() == 3
        assert db.query(SkillNormalizationCandidate).filter_by(
            raw_skill="尚未归一化技能"
        ).count() == 1


def test_matching_recomputes_when_prediction_input_changes():
    token = _token("prediction_cache_match")
    predicted_id, _ = _prediction(token)
    _comparison_targets(predicted_id)
    first = client.post(
        f"/api/v1/predicted-positions/{predicted_id}/matches/tasks",
        headers=_headers(token),
    ).json()["data"]["results"]
    assert {item["version"] for item in first} == {1}

    with SessionLocal() as db:
        row = db.get(PredictedPosition, predicted_id)
        row.potential_skills = [*row.potential_skills, "新增技能"]
        row.updated_at = datetime.now(timezone.utc)
        db.commit()

    second = client.post(
        f"/api/v1/predicted-positions/{predicted_id}/matches/tasks",
        headers=_headers(token),
    ).json()["data"]["results"]
    assert {item["version"] for item in second} == {2}
    assert {item["match_id"] for item in second}.isdisjoint(
        item["match_id"] for item in first
    )


def test_relation_mutation_rejects_stale_history_versions():
    token = _token("relation_stale_versions")
    predicted_id, _ = _prediction(token)
    headers = _headers(token)
    relation = client.post(
        f"/api/v1/predicted-positions/{predicted_id}/relations",
        headers=headers,
        json={"relation_type": "independent", "reason": "v1"},
    ).json()["data"]
    updated = client.put(
        f"/api/v1/predicted-positions/{predicted_id}/relations/{relation['relation_id']}",
        headers=headers,
        json={"relation_type": "independent", "reason": "v2"},
    ).json()["data"]

    stale_update = client.put(
        f"/api/v1/predicted-positions/{predicted_id}/relations/{relation['relation_id']}",
        headers=headers,
        json={"relation_type": "independent", "reason": "stale update"},
    )
    stale_delete = client.delete(
        f"/api/v1/predicted-positions/{predicted_id}/relations/{relation['relation_id']}",
        headers=headers,
    )
    assert stale_update.status_code == 422
    assert stale_delete.status_code == 422

    deleted = client.delete(
        f"/api/v1/predicted-positions/{predicted_id}/relations/{updated['relation_id']}",
        headers=headers,
    )
    assert deleted.status_code == 200
    assert deleted.json()["data"]["status"] == "deleted"

    for stale_id in (relation["relation_id"], updated["relation_id"]):
        assert client.put(
            f"/api/v1/predicted-positions/{predicted_id}/relations/{stale_id}",
            headers=headers,
            json={"relation_type": "independent", "reason": "stale"},
        ).status_code == 422
        assert client.delete(
            f"/api/v1/predicted-positions/{predicted_id}/relations/{stale_id}",
            headers=headers,
        ).status_code == 422


def test_definition_recomputes_when_prediction_input_changes():
    token = _token("prediction_cache_definition")
    predicted_id, _ = _prediction(token)
    _comparison_targets(predicted_id)
    generated = client.post(
        f"/api/v1/predicted-positions/{predicted_id}/definition-drafts",
        headers=_headers(token),
    ).json()["data"]

    with SessionLocal() as db:
        row = db.get(PredictedPosition, predicted_id)
        row.potential_skills = [*row.potential_skills, "新增技能"]
        row.updated_at = datetime.now(timezone.utc)
        db.commit()

    regenerated = client.post(
        f"/api/v1/predicted-positions/{predicted_id}/definition-drafts",
        headers=_headers(token),
    ).json()["data"]

    assert regenerated["definition_id"] != generated["definition_id"]
    assert regenerated["version"] == generated["version"] + 1


def test_matching_recomputes_when_target_profile_changes():
    token = _token("prediction_cache_target")
    predicted_id, _ = _prediction(token)
    _comparison_targets(predicted_id)
    first = client.post(
        f"/api/v1/predicted-positions/{predicted_id}/matches/tasks",
        headers=_headers(token),
    ).json()["data"]["results"]
    assert {item["version"] for item in first} == {1}

    with SessionLocal() as db:
        target = db.query(StandardPosition).first()
        target.required_skills = [
            *target.required_skills,
            {"skill_id": "new-skill", "skill_name": "新技能"},
        ]
        target.updated_at = datetime.now(timezone.utc)
        db.commit()

    second = client.post(
        f"/api/v1/predicted-positions/{predicted_id}/matches/tasks",
        headers=_headers(token),
    ).json()["data"]["results"]

    assert {item["version"] for item in second} == {2}
    assert {item["match_id"] for item in second}.isdisjoint(
        item["match_id"] for item in first
    )


def test_matching_recomputes_when_skill_catalog_changes():
    token = _token("prediction_cache_catalog")
    predicted_id, _ = _prediction(token)
    _comparison_targets(predicted_id)
    first = client.post(
        f"/api/v1/predicted-positions/{predicted_id}/matches/tasks",
        headers=_headers(token),
    ).json()["data"]["results"]
    assert {item["version"] for item in first} == {1}

    with SessionLocal() as db:
        db.add(Skill(skill_name="缓存新增技能", category="人工智能"))
        db.commit()

    second = client.post(
        f"/api/v1/predicted-positions/{predicted_id}/matches/tasks",
        headers=_headers(token),
    ).json()["data"]["results"]

    assert {item["version"] for item in second} == {2}


def _prediction_record() -> PredictedPositionRecord:
    return PredictedPositionRecord(
        predicted_id="prediction-1",
        position_name="AI工程师",
        prediction_basis=(),
        related_source_ids=(),
        potential_responsibilities=(),
        potential_skills=("Python",),
        industry_scenarios=(),
        confidence_score=0.8,
        status="candidate",
        created_at=None,
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _target_profile(target_id: str) -> PositionComparisonProfile:
    return PositionComparisonProfile(
        target_type="standard_position",
        target_id=target_id,
        name="后端工程师",
        skill_ids=("skill_python",),
        skill_names=("Python",),
        responsibilities=(),
        industry_scenarios=(),
        evidence_references=(),
    )


def test_cache_key_changes_with_algorithm_catalog_and_target_versions():
    prediction = _prediction_record()
    source = _target_profile("source")
    targets = (_target_profile("target"),)
    base = ManagePredictedPositionsApplication(
        uow_factory=lambda: None,
        tasks=None,
        gateway=None,
    )

    baseline = base._matching_cache_key(prediction, source, targets, "catalog-v1")
    algorithm_changed = replace(
        base,
        algorithm_version="market-prediction-v2",
    )._matching_cache_key(prediction, source, targets, "catalog-v1")
    catalog_changed = base._matching_cache_key(
        prediction, source, targets, "catalog-v2"
    )
    target_changed = base._matching_cache_key(
        prediction, source, (_target_profile("target-2"),), "catalog-v1"
    )

    assert len(
        {baseline, algorithm_changed, catalog_changed, target_changed}
    ) == 4

    definition_v1 = base._definition_cache_key(prediction, "matching-v1", "catalog-v1")
    definition_v2 = base._definition_cache_key(prediction, "matching-v2", "catalog-v1")
    definition_v3 = base._definition_cache_key(prediction, "matching-v2", "catalog-v2")
    assert len({definition_v1, definition_v2, definition_v3}) == 3


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda facts: facts.update(task_status="failed"), "REMOTE_ANALYSIS_NOT_SUCCEEDED"),
        (lambda facts: facts["task_result"].update(mock=True), "MOCK_RESULT_NOT_PUBLISHABLE"),
        (lambda facts: facts["task_result"].update(source_coverage=0.2), "SOURCE_COVERAGE_BELOW_THRESHOLD"),
        (lambda facts: facts["task_result"].update(quality_flags=["blocking"]), "UNRESOLVED_HIGH_RISK_FLAGS"),
        (lambda facts: facts["definition"].update(core_responsibilities=[]), "INCOMPLETE_DEFINITION"),
        (lambda facts: facts["definition"]["required_skills"][0].update(skill_id=None), "CORE_SKILLS_NOT_NORMALIZED"),
        (lambda facts: facts["definition"]["evidence_by_conclusion"].update(position_name=[]), "INCOMPLETE_EVIDENCE_REFERENCES"),
        (lambda facts: facts.update(review_status="pending"), "REVIEW_NOT_APPROVED"),
    ],
)
def test_each_publication_gate_fails_independently(mutation, expected: str) -> None:
    evidence = ["snapshot:1"]
    facts = {
        "provider_run_id": "provider-run-1",
        "task_status": "succeeded",
        "task_result": {"mock": False, "source_coverage": 1.0, "quality_flags": []},
        "review_status": "approved",
        "definition": {
            "position_name": "AI训练师",
            "core_responsibilities": ["训练模型"],
            "required_skills": [{"skill_id": "skill-1", "resolution_status": "resolved"}],
            "industry_scenarios": ["人工智能"],
            "formation_basis": [{"source": "trend"}],
            "evidence_by_conclusion": {
                "position_name": evidence,
                "core_responsibilities": evidence,
                "required_skills": evidence,
                "industry_scenarios": evidence,
                "formation_basis": evidence,
            },
        },
    }
    mutation(facts)
    errors = ManagePredictedPositions._definition_gate_errors(
        deepcopy(facts), 0.6, ("blocking",)
    )
    assert expected in errors


def test_definition_transaction_rolls_back_normalization_candidate(monkeypatch) -> None:
    token = _token("prediction_workflow_rollback")
    predicted_id, _ = _prediction(token)
    with SessionLocal() as db:
        prediction = db.get(PredictedPosition, predicted_id)
        prediction.potential_skills = ["事务内未知技能"]
        db.commit()

    def fail_save(*args, **kwargs):
        raise RuntimeError("definition persistence failed")

    monkeypatch.setattr(SqlAlchemyPredictedPositionRepository, "save_definition", fail_save)
    with pytest.raises(RuntimeError, match="definition persistence failed"):
        client.post(
            f"/api/v1/predicted-positions/{predicted_id}/definition-drafts",
            headers=_headers(token),
        )
    with SessionLocal() as db:
        assert db.query(PredictedPositionDefinitionVersion).count() == 0
        assert db.query(SkillNormalizationCandidate).filter_by(
            raw_skill="事务内未知技能"
        ).count() == 0
