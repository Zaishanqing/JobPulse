from dataclasses import replace
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from tests.runtime_database import reset_database_data, SessionLocal
from app.main import app
from app.models.standard_position import StandardPosition
from app.models.knowledge_graph_mapping import KnowledgeGraphEntityMapping
from app.models.task_record import TaskRecord
from app.models.trend_report import TrendReport, TrendReportReviewAdjustment
from app.models.skill import Skill
from app.models.skill_alias import SkillAlias
from app.models.skill_normalization_candidate import SkillNormalizationCandidate
from app.services import trend_analysis_service
from app.contexts.market_intelligence.ports import (
    PositionSkillTrendResultV1,
    PositionSkillTrendRunV1,
)
from app.domain.json_types import freeze_json_object
from tests.user_factory import create_internal_user


client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_database():
    reset_database_data()
    yield
    reset_database_data()


def _register_and_login(username: str, role: str) -> str:
    create_internal_user(username, role)
    client.post(
        "/api/v1/auth/register",
        json={
            "role": role,
            "username": username,
            "password": "password123",
            "email": f"{username}@example.com",
            "phone": "13800000000",
        },
    )
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "password123"},
    )
    return response.json()["data"]["access_token"]


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_java_position() -> str:
    now = datetime.now(timezone.utc).isoformat()
    db = SessionLocal()
    try:
        catalog = (
            ("skill_java", "Java", "编程语言"),
            ("skill_spring_boot", "Spring Boot", "框架技术"),
            ("skill_mysql", "MySQL", "数据库"),
            ("skill_servlet", "Servlet", "传统 Web"),
            ("skill_docker", "Docker", "云原生"),
        )
        db.add_all(Skill(id=skill_id, skill_name=name, category=category) for skill_id, name, category in catalog)
        db.flush()
        db.add(SkillAlias(skill_id="skill_spring_boot", alias="SpringBoot"))
        position = StandardPosition(
            position_name="Java 开发工程师",
            core_responsibilities=["负责 Java 后端服务设计、开发与性能优化"],
            required_skills=[
                {
                    "skill_id": "skill_java",
                    "skill_name": "Java",
                    "category": "编程语言",
                    "weight": 0.3,
                    "confidence": 0.96,
                    "importance_level": "core",
                    "trend_score": 0.12,
                    "evidence_count": 128,
                },
                {
                    "skill_id": "skill_spring_boot",
                    "skill_name": "Spring Boot",
                    "category": "框架技术",
                    "weight": 0.24,
                    "confidence": 0.94,
                    "importance_level": "high",
                    "trend_score": 0.35,
                    "evidence_count": 96,
                },
                {
                    "skill_id": "skill_mysql",
                    "skill_name": "MySQL",
                    "category": "数据库",
                    "weight": 0.16,
                    "confidence": 0.9,
                    "importance_level": "high",
                    "trend_score": -0.15,
                    "evidence_count": 74,
                },
                {
                    "skill_id": "skill_servlet",
                    "skill_name": "Servlet",
                    "category": "传统 Web",
                    "weight": 0.06,
                    "confidence": 0.8,
                    "importance_level": "edge",
                    "trend_score": -0.22,
                    "evidence_count": 12,
                },
            ],
            bonus_skills=[
                {
                    "skill_id": "skill_docker",
                    "skill_name": "Docker",
                    "category": "云原生",
                    "weight": 0.12,
                    "confidence": 0.88,
                    "importance_level": "bonus",
                    "trend_score": 0.28,
                    "evidence_count": 38,
                    "created_at": now,
                }
            ],
            industry_scenarios=["互联网后端服务", "企业业务系统"],
            status="existing",
        )
        db.add(position)
        db.flush()
        db.add(
            KnowledgeGraphEntityMapping(
                entity_type="position",
                main_system_id=position.id,
                knowledge_graph_id=position.id,
                sync_status="synced",
            )
        )
        db.commit()
        db.refresh(position)
        return position.id
    finally:
        db.close()


def _create_trend_report(token: str, position_id: str) -> str:
    response = client.post(
        f"/api/v1/positions/{position_id}/trend-analysis/tasks",
        json={"time_window_start": "2026-01-01", "time_window_end": "2026-07-01"},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "completed"

    task_response = client.get(
        f"/api/v1/trend-analysis/tasks/{data['task_id']}",
        headers=_auth_headers(token),
    )
    assert task_response.status_code == 200
    return data["report_id"]


def test_create_standard_position_and_trend_analysis_task():
    admin_token = _register_and_login("trend_admin001", "admin")
    position_id = _create_java_position()

    report_id = _create_trend_report(admin_token, position_id)

    assert report_id

    with SessionLocal() as db:
        report = db.query(TrendReport).filter_by(id=report_id).one()
        assert report.graph_version_id == "1"


def test_trend_analysis_rejects_position_without_standard_skills_before_task_creation():
    admin_token = _register_and_login("trend_empty_graph", "admin")
    with SessionLocal() as db:
        position = StandardPosition(
            position_name="无技能岗位",
            core_responsibilities=[],
            required_skills=[],
            bonus_skills=[],
            industry_scenarios=[],
            status="existing",
        )
        db.add(position)
        db.flush()
        db.add(
            KnowledgeGraphEntityMapping(
                entity_type="position",
                main_system_id=position.id,
                knowledge_graph_id=position.id,
                sync_status="synced",
            )
        )
        db.commit()
        db.refresh(position)
        position_id = position.id

    response = client.post(
        f"/api/v1/positions/{position_id}/trend-analysis/tasks",
        headers=_auth_headers(admin_token),
    )

    assert response.status_code == 400
    assert "当前岗位图谱没有标准技能" in response.json()["message"]
    with SessionLocal() as db:
        assert db.query(TaskRecord).count() == 0


@pytest.mark.parametrize("failure_stage", ["report", "task"])
def test_trend_analysis_rolls_back_every_partial_write_and_retry_is_clean(
    monkeypatch, failure_stage: str
):
    token = _register_and_login(f"trend_atomic_{failure_stage}", "admin")
    position_id = _create_java_position()
    original_report = trend_analysis_service._flush_trend_report
    original_task = trend_analysis_service.create_succeeded_task

    if failure_stage == "report":
        def fail_after_report(*args, **kwargs):
            original_report(*args, **kwargs)
            raise RuntimeError("injected after report flush")

        monkeypatch.setattr(trend_analysis_service, "_flush_trend_report", fail_after_report)
    else:
        def fail_during_task(*args, **kwargs):
            original_task(*args, **kwargs)
            raise RuntimeError("injected during task creation")

        monkeypatch.setattr(trend_analysis_service, "create_succeeded_task", fail_during_task)

    no_raise_client = TestClient(app, raise_server_exceptions=False)
    failed = no_raise_client.post(
        f"/api/v1/positions/{position_id}/trend-analysis/tasks",
        headers=_auth_headers(token),
    )
    assert failed.status_code == 500
    with SessionLocal() as db:
        expected_initial = 0 if failure_stage == "task" else 1
        assert db.query(TrendReport).count() == 0
        assert db.query(TaskRecord).count() == expected_initial
        assert db.query(StandardPosition).filter_by(id=position_id).one().id == position_id

    monkeypatch.setattr(trend_analysis_service, "_flush_trend_report", original_report)
    monkeypatch.setattr(trend_analysis_service, "create_succeeded_task", original_task)
    if failure_stage == "report":
        with SessionLocal() as db:
            existing_task_id = db.query(TaskRecord).one().id
        retried = client.get(
            f"/api/v1/trend-analysis/tasks/{existing_task_id}",
            headers=_auth_headers(token),
        )
    else:
        retried = client.post(
            f"/api/v1/positions/{position_id}/trend-analysis/tasks",
            headers=_auth_headers(token),
        )
    assert retried.status_code == 200
    task_id = retried.json()["data"]["task_id"]
    with SessionLocal() as db:
        report = db.query(TrendReport).one()
        task = db.query(TaskRecord).filter_by(id=task_id).one()
        assert report.graph_version_id == "1"
        assert task.result_payload["report_id"] == report.id
        assert task.status == "succeeded"


def test_get_trend_report_detail_contains_full_graph():
    admin_token = _register_and_login("trend_admin002", "admin")
    position_id = _create_java_position()
    report_id = _create_trend_report(admin_token, position_id)

    response = client.get(
        f"/api/v1/trend-reports/{report_id}",
        headers=_auth_headers(admin_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["report_id"] == report_id
    assert data["analysis_mode"] == "remote_multi_source"
    assert data["provider"] == "trend_intelligence_http"
    assert data["current_graph"]["position_name"] == "Java 开发工程师"
    assert len(data["current_graph"]["skills"]) >= 4
    assert data["graph_version"]
    assert data["current_graph"]["graph_version"] == data["graph_version"]
    assert "rising_skills" in data
    assert "risks" in data


def test_get_current_graph():
    admin_token = _register_and_login("trend_admin003", "admin")
    position_id = _create_java_position()
    report_id = _create_trend_report(admin_token, position_id)

    response = client.get(
        f"/api/v1/trend-reports/{report_id}/current-graph",
        headers=_auth_headers(admin_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["position_id"] == position_id
    assert any(skill["skill_name"] == "Java" for skill in data["skills"])
    assert any(relation["relation_type"] == "requires" for relation in data["relations"])


def test_get_skill_weight_distribution():
    admin_token = _register_and_login("trend_admin004", "admin")
    position_id = _create_java_position()
    report_id = _create_trend_report(admin_token, position_id)

    response = client.get(
        f"/api/v1/trend-reports/{report_id}/skill-weight-distribution",
        headers=_auth_headers(admin_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert any(skill["skill_name"] == "Java" for skill in data["core"])
    assert any(skill["skill_name"] == "Docker" for skill in data["bonus"])


def test_get_new_rising_declining_skills():
    admin_token = _register_and_login("trend_admin005", "admin")
    position_id = _create_java_position()
    report_id = _create_trend_report(admin_token, position_id)

    new_response = client.get(
        f"/api/v1/trend-reports/{report_id}/new-skills",
        headers=_auth_headers(admin_token),
    )
    rising_response = client.get(
        f"/api/v1/trend-reports/{report_id}/rising-skills",
        headers=_auth_headers(admin_token),
    )
    declining_response = client.get(
        f"/api/v1/trend-reports/{report_id}/declining-skills",
        headers=_auth_headers(admin_token),
    )

    assert new_response.status_code == 200
    assert rising_response.status_code == 200
    assert declining_response.status_code == 200
    assert any(skill["skill_name"] == "Docker" for skill in new_response.json()["data"])
    assert any(skill["skill_name"] == "Spring Boot" for skill in rising_response.json()["data"])
    assert any(skill["skill_name"] == "MySQL" for skill in declining_response.json()["data"])


def test_get_replaced_combo_risks_and_summary():
    admin_token = _register_and_login("trend_admin006", "admin")
    position_id = _create_java_position()
    report_id = _create_trend_report(admin_token, position_id)

    replaced_response = client.get(
        f"/api/v1/trend-reports/{report_id}/replaced-skills",
        headers=_auth_headers(admin_token),
    )
    combo_response = client.get(
        f"/api/v1/trend-reports/{report_id}/skill-combo-shifts",
        headers=_auth_headers(admin_token),
    )
    risks_response = client.get(
        f"/api/v1/trend-reports/{report_id}/risks",
        headers=_auth_headers(admin_token),
    )
    summary_response = client.get(
        f"/api/v1/trend-reports/{report_id}/summary",
        headers=_auth_headers(admin_token),
    )

    assert replaced_response.status_code == 200
    assert combo_response.status_code == 200
    assert risks_response.status_code == 200
    assert summary_response.status_code == 200
    assert replaced_response.json()["data"] == []
    assert len(combo_response.json()["data"]) >= 1
    risk_types = {risk["risk_type"] for risk in risks_response.json()["data"]}
    assert risk_types == {"data_quality"}
    assert "Java 开发工程师" in summary_response.json()["data"]["summary"]


def test_edit_and_publish_trend_report():
    admin_token = _register_and_login("trend_admin007", "admin")
    position_id = _create_java_position()
    report_id = _create_trend_report(admin_token, position_id)

    edit_response = client.put(
        f"/api/v1/trend-reports/{report_id}",
        json={
            "reason": "优化竞赛展示摘要",
            "summary": "人工修订后的 Java 岗位趋势报告摘要",
        },
        headers=_auth_headers(admin_token),
    )
    publish_response = client.post(
        f"/api/v1/trend-reports/{report_id}/publish",
        headers=_auth_headers(admin_token),
    )

    assert edit_response.status_code == 200
    assert edit_response.json()["data"]["summary"] == "人工修订后的 Java 岗位趋势报告摘要"
    assert edit_response.json()["data"]["algorithm_result"]["summary"] != edit_response.json()["data"]["reviewed_result"]["summary"]
    with SessionLocal() as db:
        report = db.get(TrendReport, report_id)
        adjustment = db.query(TrendReportReviewAdjustment).one()
        assert report.summary != "人工修订后的 Java 岗位趋势报告摘要"
        assert adjustment.actor_user_id
        assert adjustment.reason == "优化竞赛展示摘要"
        assert adjustment.before_values["summary"] == report.summary
        assert adjustment.after_values["summary"] == "人工修订后的 Java 岗位趋势报告摘要"
        assert adjustment.created_at is not None
    blocked = publish_response
    assert blocked.status_code == 400
    review = client.post(
        "/api/v1/review-tasks",
        json={"object_type": "trend_report", "object_id": report_id},
        headers=_auth_headers(admin_token),
    )
    assert review.status_code == 200
    review_id = review.json()["data"]["task_id"]
    assert client.post(f"/api/v1/review-tasks/{review_id}/claim", headers=_auth_headers(admin_token)).status_code == 200
    assert client.post(f"/api/v1/review-tasks/{review_id}/approve", headers=_auth_headers(admin_token)).status_code == 200
    publish_response = client.post(
        f"/api/v1/trend-reports/{report_id}/publish",
        headers=_auth_headers(admin_token),
    )
    assert publish_response.status_code == 200
    assert publish_response.json()["data"]["status"] == "published"


def test_algorithm_lineage_cannot_be_changed_and_published_report_is_immutable():
    token = _register_and_login("trend_immutable", "admin")
    position_id = _create_java_position()
    report_id = _create_trend_report(token, position_id)

    rejected = client.put(
        f"/api/v1/trend-reports/{report_id}",
        json={"reason": "非法覆盖", "provider_run_id": "forged-run"},
        headers=_auth_headers(token),
    )
    assert rejected.status_code == 422
    with SessionLocal() as db:
        original_run_id = db.get(TrendReport, report_id).provider_run_id

    review = client.post(
        "/api/v1/review-tasks",
        json={"object_type": "trend_report", "object_id": report_id},
        headers=_auth_headers(token),
    ).json()["data"]
    client.post(f"/api/v1/review-tasks/{review['task_id']}/claim", headers=_auth_headers(token))
    client.post(f"/api/v1/review-tasks/{review['task_id']}/approve", headers=_auth_headers(token))
    assert client.post(
        f"/api/v1/trend-reports/{report_id}/publish", headers=_auth_headers(token)
    ).status_code == 200
    immutable = client.put(
        f"/api/v1/trend-reports/{report_id}",
        json={"reason": "发布后修改", "summary": "不应生效"},
        headers=_auth_headers(token),
    )
    assert immutable.status_code == 400
    with SessionLocal() as db:
        report = db.get(TrendReport, report_id)
        assert report.provider_run_id == original_run_id
        assert db.query(TrendReportReviewAdjustment).count() == 0


def test_remote_state_alias_mapping_and_idempotent_projection(monkeypatch):
    token = _register_and_login("trend_remote_state", "admin")
    position_id = _create_java_position()
    use_case = app.state.container.trend_reports
    gateway = use_case.gateway
    original_create = gateway.create_position_skill_trend
    original_get = gateway.get_position_skill_trend_run

    def create_pending(request):
        created = original_create(request)
        return replace(created, status="pending")

    monkeypatch.setattr(gateway, "create_position_skill_trend", create_pending)
    monkeypatch.setattr(
        gateway, "get_position_skill_trend_run",
        lambda run_id: PositionSkillTrendRunV1(run_id, "running", "test-skill-fingerprint"),
    )
    created = client.post(
        f"/api/v1/positions/{position_id}/trend-analysis/tasks",
        headers=_auth_headers(token),
    ).json()["data"]
    assert created["canonical_status"] == "pending"
    running = client.get(
        f"/api/v1/trend-analysis/tasks/{created['task_id']}", headers=_auth_headers(token)
    ).json()["data"]
    assert running["canonical_status"] == "running"
    monkeypatch.setattr(gateway, "get_position_skill_trend_run", original_get)
    completed = client.get(
        f"/api/v1/trend-analysis/tasks/{created['task_id']}", headers=_auth_headers(token)
    ).json()["data"]
    assert completed["canonical_status"] == "succeeded"
    run_id = completed["provider_run_id"]
    request = gateway.skill_requests[run_id]
    spring = next(item for item in request.standard_skills if item["skill_id"] == "skill_spring_boot")
    assert tuple(spring["aliases"]) == ("SpringBoot",)

    remote = original_get(run_id)
    payload = gateway.get_position_skill_trend_result(run_id).payload
    use_case._commit_projection(created["task_id"], remote, payload)
    with SessionLocal() as db:
        row = db.query(TrendReport).filter_by(provider_run_id=run_id).one()
        assert row.skill_trend_details
        assert {
            "growth_rate",
            "trend_direction",
            "evidence_references",
            "quality_flags",
            "score_explanation",
            "current_window_signal",
            "historical_window_signal",
        } <= set(row.skill_trend_details[0])


def test_background_synchronization_projects_task_without_browser_poll(monkeypatch):
    token = _register_and_login("trend_background_sync", "admin")
    position_id = _create_java_position()
    use_case = app.state.container.trend_reports
    gateway = use_case.gateway
    original_create = gateway.create_position_skill_trend

    def create_pending(request):
        created = original_create(request)
        return replace(created, status="pending")

    monkeypatch.setattr(gateway, "create_position_skill_trend", create_pending)
    created = client.post(
        f"/api/v1/positions/{position_id}/trend-analysis/tasks",
        headers=_auth_headers(token),
    ).json()["data"]
    assert created["canonical_status"] == "pending"

    assert use_case.synchronize_active_tasks() == 1
    with SessionLocal() as db:
        task = db.get(TaskRecord, created["task_id"])
        assert task.status == "succeeded"
        assert db.query(TrendReport).filter_by(
            provider_run_id=task.result_payload["provider_run_id"]
        ).count() == 1


def test_remote_failure_is_not_reported_as_success(monkeypatch):
    token = _register_and_login("trend_remote_failure", "admin")
    position_id = _create_java_position()
    gateway = app.state.container.trend_reports.gateway
    original_create = gateway.create_position_skill_trend

    def create_failed(request):
        created = original_create(request)
        return replace(created, status="failed", error_message="upstream collection failed")

    monkeypatch.setattr(gateway, "create_position_skill_trend", create_failed)
    response = client.post(
        f"/api/v1/positions/{position_id}/trend-analysis/tasks",
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    assert response.json()["data"]["canonical_status"] == "failed"
    assert response.json()["data"]["mock"] is False
    with SessionLocal() as db:
        assert db.query(TrendReport).count() == 0


def test_trend_report_permissions_hide_drafts_from_published_readers():
    admin = _register_and_login("trend_permission_admin", "admin")
    reviewer = _register_and_login("trend_permission_reviewer", "reviewer")
    reader = _register_and_login("trend_permission_reader", "enterprise_user")
    position_id = _create_java_position()
    denied = client.post(
        f"/api/v1/positions/{position_id}/trend-analysis/tasks",
        headers=_auth_headers(reviewer),
    )
    assert denied.status_code == 403
    report_id = _create_trend_report(admin, position_id)
    assert client.get(
        f"/api/v1/trend-reports/{report_id}", headers=_auth_headers(reviewer)
    ).status_code == 200
    assert client.get(
        f"/api/v1/trend-reports/{report_id}", headers=_auth_headers(reader)
    ).status_code == 404
    listed = client.get(
        f"/api/v1/positions/{position_id}/trend-reports", headers=_auth_headers(reader)
    )
    assert listed.status_code == 200
    assert listed.json()["data"]["items"] == []
    assert listed.json()["data"]["pagination"]["total"] == 0


def test_unresolved_remote_terms_enter_normalization_candidates(monkeypatch):
    token = _register_and_login("trend_unresolved", "admin")
    position_id = _create_java_position()
    gateway = app.state.container.trend_reports.gateway
    original_result = gateway.get_position_skill_trend_result

    def result_with_unresolved(run_id):
        payload = dict(original_result(run_id).payload)
        payload["unresolved_terms"] = [
            {"term": "Kubernetes operator engineering", "evidence_references": ["test-snapshot-1"]}
        ]
        return PositionSkillTrendResultV1(freeze_json_object(payload))

    monkeypatch.setattr(gateway, "get_position_skill_trend_result", result_with_unresolved)
    report_id = _create_trend_report(token, position_id)
    detail = client.get(
        f"/api/v1/trend-reports/{report_id}", headers=_auth_headers(token)
    ).json()["data"]
    assert detail["unresolved_terms"][0]["term"] == "Kubernetes operator engineering"
    with SessionLocal() as db:
        candidate = db.query(SkillNormalizationCandidate).one()
        assert candidate.raw_skill == "Kubernetes operator engineering"
        assert candidate.context == f"trend_report:{detail['provider_run_id']}"


def test_overlong_unresolved_terms_do_not_rollback_succeeded_report(monkeypatch):
    token = _register_and_login("trend_overlong_unresolved", "admin")
    position_id = _create_java_position()
    gateway = app.state.container.trend_reports.gateway
    original_result = gateway.get_position_skill_trend_result
    overlong_term = "x" * 129

    def result_with_overlong_unresolved(run_id):
        payload = dict(original_result(run_id).payload)
        payload["unresolved_terms"] = [
            {"term": "Kubernetes operator engineering", "evidence_references": ["test-snapshot-1"]},
            {"term": overlong_term, "evidence_references": ["test-snapshot-2"]},
        ]
        return PositionSkillTrendResultV1(freeze_json_object(payload))

    monkeypatch.setattr(gateway, "get_position_skill_trend_result", result_with_overlong_unresolved)
    report_id = _create_trend_report(token, position_id)
    detail = client.get(
        f"/api/v1/trend-reports/{report_id}", headers=_auth_headers(token)
    ).json()["data"]

    assert [item["term"] for item in detail["unresolved_terms"]] == [
        "Kubernetes operator engineering",
        overlong_term,
    ]
    assert detail["summary"] == "Java 开发工程师：已基于多来源数据完成 5 项标准技能趋势分析。"
    with SessionLocal() as db:
        assert [row.raw_skill for row in db.query(SkillNormalizationCandidate).all()] == [
            "Kubernetes operator engineering"
        ]


@pytest.mark.parametrize(
    ("gate", "expected"),
    [
        ("coverage", "SOURCE_COVERAGE_BELOW_THRESHOLD"),
        ("quality", "UNRESOLVED_HIGH_RISK_FLAGS"),
        ("graph", "GRAPH_VERSION_INPUT_MISMATCH"),
        ("remote", "REMOTE_ANALYSIS_NOT_SUCCEEDED"),
        ("mock", "MOCK_RESULT_NOT_PUBLISHABLE"),
        ("normalization", "CORE_SKILLS_NOT_NORMALIZED"),
        ("lineage", "ALGORITHM_LINEAGE_MISMATCH"),
    ],
)
def test_publication_gate_failures_are_explicit(gate, expected):
    token = _register_and_login(f"trend_gate_{gate}", "admin")
    position_id = _create_java_position()
    report_id = _create_trend_report(token, position_id)
    review = client.post(
        "/api/v1/review-tasks", json={"object_type": "trend_report", "object_id": report_id},
        headers=_auth_headers(token),
    ).json()["data"]
    client.post(f"/api/v1/review-tasks/{review['task_id']}/claim", headers=_auth_headers(token))
    client.post(f"/api/v1/review-tasks/{review['task_id']}/approve", headers=_auth_headers(token))
    with SessionLocal() as db:
        report = db.get(TrendReport, report_id)
        task = db.query(TaskRecord).filter(TaskRecord.result_reference == f"trend_report:{report_id}").one()
        if gate == "coverage":
            report.source_coverage = 0.1
        elif gate == "quality":
            report.quality_flags = ["high_risk"]
        elif gate == "graph":
            task.result_payload = {**task.result_payload, "graph_version_id": "wrong"}
        elif gate == "remote":
            task.status = "failed"
        elif gate == "mock":
            task.result_payload = {**task.result_payload, "mock": True}
        elif gate == "normalization":
            db.add(SkillNormalizationCandidate(
                raw_skill="unresolved core", context=f"trend_report:{report.provider_run_id}",
                status="pending", confidence=0,
            ))
        else:
            report.algorithm_version = "tampered-algorithm-version"
        db.commit()
    response = client.post(
        f"/api/v1/trend-reports/{report_id}/publish", headers=_auth_headers(token)
    )
    assert response.status_code == 400
    assert expected in response.text


def test_trend_run_and_report_pagination_filter_and_batch_contract():
    token = _register_and_login("trend_delivery_admin", "admin")
    position_id = _create_java_position()
    report_id = _create_trend_report(token, position_id)

    reports = client.get(
        f"/api/v1/positions/{position_id}/trend-reports?page=1&page_size=1&sort_order=desc",
        headers=_auth_headers(token),
    ).json()["data"]
    assert reports["pagination"] == {"page": 1, "page_size": 1, "total": 1, "total_pages": 1}
    assert reports["items"][0]["resource_type"] == "trend_report"
    skill_trend = reports["items"][0]["skill_trends"][0]
    assert {
        "growth_rate",
        "trend_direction",
        "evidence_references",
        "quality_flags",
        "score_explanation",
        "current_window_signal",
        "historical_window_signal",
        "category",
        "weight",
        "importance_level",
    } <= set(skill_trend)

    batch = client.post(
        "/api/v1/trend-reports/batch-query",
        json={"ids": [report_id, report_id, "missing-report"]},
        headers=_auth_headers(token),
    ).json()["data"]
    assert [item["report_id"] for item in batch["items"]] == [report_id]
    assert batch["not_found_ids"] == ["missing-report"]

    runs = client.get(
        "/api/v1/trend-runs?run_type=position_skill_trend_run&status=succeeded",
        headers=_auth_headers(token),
    ).json()["data"]
    assert runs["pagination"]["total"] == 1
    assert runs["items"][0]["resource_type"] == "position_skill_trend_run"
