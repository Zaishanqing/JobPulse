from __future__ import annotations

import gzip
import json
from datetime import date
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as Namespace

import pytest
from fastapi.testclient import TestClient

from app.contexts.emerging_positions.domain import (
    EmergingCandidate,
    EmergingRecord,
)
from app.domain.json_types import freeze_json_object
from app.main import app
from app.contexts.market_intelligence import TrendReportRecord
from app.contexts.insight_cards.release_registry import (
    ManifestReleaseRegistry,
    ReleaseNotFound,
)
from app.contexts.source_jds import SourceJDNotFound
from app.domain.trend_analysis import (
    SkillWeightDistribution,
    TrendGraphSnapshot,
    TrendSkill,
)
from app.models.evidence_source import EvidenceSource
from tests.runtime_database import SessionLocal, reset_database_data
from tests.user_factory import create_internal_user
from tests.test_release_membership import _fact as _published_fact


client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_database_and_overrides():
    reset_database_data()
    yield
    app.dependency_overrides.clear()
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


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _emerging_record() -> EmergingRecord:
    candidate = EmergingCandidate.create(
        candidate_id="emerging-1",
        cluster_id="cluster-1",
        position_name="AI Prompt Engineer",
        core_responsibilities=("design prompts",),
        required_skills=({"skill_id": "s1", "level": "required"},),
        bonus_skills=(),
        industry_scenarios=("internet",),
        germination_score=0.9,
        score_dimensions={"novelty": 0.8},
        evidence_jd_ids=("jd-1",),
        status="published",
    )
    return EmergingRecord(candidate=candidate, created_at=None, updated_at=None)


def _trend_record() -> TrendReportRecord:
    skill = TrendSkill(
        skill_id="s1",
        skill_name="Skill",
        category="c",
        weight=1.0,
        confidence=0.9,
        importance_level="core",
        trend_score=0.8,
        evidence_count=1,
    )
    graph = TrendGraphSnapshot(
        position_id="pos-1",
        position_name="Position",
        graph_version="gv-9",
        skills=(skill,),
        relations=(),
        core_responsibilities=(),
        industry_scenarios=(),
        status="published",
    )
    return TrendReportRecord(
        report_id="report-1",
        position_id="pos-1",
        graph_version_id="gv-9",
        time_window_start=date(2026, 1, 1),
        time_window_end=date(2026, 3, 31),
        current_graph=graph,
        skill_weight_distribution=SkillWeightDistribution(
            core=(), high=(), bonus=(), edge=()
        ),
        new_skills=(),
        rising_skills=(),
        declining_skills=(),
        replaced_skills=(),
        skill_combo_shifts=(),
        risks=(),
        summary="summary",
        status="published",
        created_at=None,
        updated_at=None,
        provider_run_id="run-1",
        algorithm_version="trend.v2",
        formula_version="formula.v1",
        skill_catalog_version="cat-3",
        source_coverage=0.8,
        evidence_references=("ev-1", "ev-2"),
    )


def test_emerging_insight_bff_returns_card() -> None:
    from app.api.dependencies.use_cases import get_emerging_position_handlers

    handlers = Namespace(
        query=Namespace(get=lambda *args: _emerging_record())
    )
    app.dependency_overrides[get_emerging_position_handlers] = (
        lambda: handlers
    )
    token = _register_and_login("insight_emerging", "admin")
    response = client.get(
        "/api/v1/innovation/insights/emerging/emerging-1",
        headers=_headers(token),
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["claim_type"] == "emerging_position"
    assert data["algorithm_version"] == "emerging-position.v1"
    assert data["evidence_algorithm_version"] == ""


def test_trend_insight_bff_returns_card() -> None:
    from app.api.dependencies.trend_reports import get_trend_report_use_cases

    use_cases = Namespace(get=lambda *args: _trend_record())
    app.dependency_overrides[get_trend_report_use_cases] = lambda: use_cases
    token = _register_and_login("insight_trend", "admin")
    response = client.get(
        "/api/v1/innovation/insights/trend/report-1",
        headers=_headers(token),
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["claim_type"] == "trend_change"
    assert data["algorithm_version"] == "trend.v2"
    assert data["algorithm_config_version"] == "formula.v1"
    assert data["source_coverage"] == 0.8


def test_evolution_insight_bff_returns_card() -> None:
    from app.api.dependencies.knowledge_graph import get_knowledge_graph_handlers

    event = {
        "event_id": "evt-1-2-skill_emergence-001",
        "event_type": "skill_emergence",
        "position_id": "pos-1",
        "from_version": 1,
        "to_version": 2,
        "source_entities": [],
        "target_entities": [{"canonical_name": "Python", "skill_id": "s1"}],
        "evidence": {
            "source": "graph_version_snapshot_diff",
            "evidence_refs": ["jd-1"],
        },
        "detector_version": "position-evolution-events.v1",
        "config_version": "config.v1",
    }
    handlers = Namespace(
        portal=lambda *args: Namespace(
            result=freeze_json_object(event),
            upstream=Namespace(trace_id="trace-1"),
        )
    )
    app.dependency_overrides[get_knowledge_graph_handlers] = lambda: handlers
    token = _register_and_login("insight_evolution", "admin")
    response = client.get(
        "/api/v1/innovation/insights/evolution/pos-1/"
        "evt-1-2-skill_emergence-001",
        headers=_headers(token),
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["claim_type"] == "role_migration"
    assert data["algorithm_version"] == "position-evolution-events.v1"
    assert data["algorithm_config_version"] == "config.v1"
    assert data["graph_version_refs"] == ["1", "2"]


def test_matching_what_if_insight_bff_returns_card() -> None:
    from app.api.dependencies.matching import get_matching_use_cases

    result = {
        "scenario_id": "scenario-1",
        "baseline_evaluation_id": "eval-1",
        "baseline_score": 60.0,
        "scenario_score": 75.0,
        "score_delta": 15.0,
        "scenario_hard_gate_status": "passed",
        "generation_status": "completed",
        "target_reachable": True,
        "algorithm_version": "counterfactual-profile.v2",
        "scoring_config_version": "scoring-config.v1",
        "evidence_refs": [
            {
                "evidence_id": "ev-1",
                "source_object_type": "matching_evidence",
                "source_object_id": "ev-1",
                "source_document_id": "doc-1",
                "source_version": "v1",
            }
        ],
    }
    use_cases = Namespace(what_if=lambda *args, **kwargs: result)
    app.dependency_overrides[get_matching_use_cases] = lambda: use_cases
    token = _register_and_login("insight_matching", "admin")
    response = client.post(
        "/api/v1/innovation/insights/matching/eval-1/what-if",
        headers=_headers(token),
        json={
            "actions": [
                {
                    "action_id": "a1",
                    "action_type": "add_skill",
                    "skill_id": "s1",
                    "estimated_hours": 10,
                }
            ]
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["claim_type"] == "matching_what_if"
    assert data["algorithm_version"] == "counterfactual-profile.v2"
    assert data["algorithm_config_version"] == "scoring-config.v1"
    assert data["next_action"] == "user_action"


def test_trend_insight_bff_reads_review_decision() -> None:
    from app.api.dependencies.trend_reports import get_trend_report_use_cases
    from app.domain.accounts import AccountActor

    use_cases = Namespace(get=lambda *args: _trend_record())
    app.dependency_overrides[get_trend_report_use_cases] = lambda: use_cases
    user_id = create_internal_user("insight_human", "admin")
    actor = AccountActor(user_id, "admin")
    container = app.state.container
    task = container.governance.reviews.create(
        actor,
        object_type="trend_report",
        object_id="report-1",
        priority="normal",
        reason="review",
    )
    container.governance.reviews.transition(
        actor, task.task_id, action="claim"
    )
    container.governance.reviews.transition(
        actor, task.task_id, action="approve", comment="ok"
    )
    token = _register_and_login("insight_human", "admin")
    response = client.get(
        "/api/v1/innovation/insights/trend/report-1",
        headers=_headers(token),
    )
    assert response.status_code == 200
    decision = response.json()["data"]["human_decision"]
    assert decision["decision"] == "approved"
    assert decision["decision_id"] == task.task_id
    assert decision["bound_object_type"] == "trend_report"
    assert decision["bound_object_id"] == "report-1"


def _write_release_manifest(
    tmp_path: Path,
    release_id: str,
    facts: tuple[dict, ...] = (),
) -> Path:
    release_dir = tmp_path / "releases"
    target = release_dir / release_id
    target.mkdir(parents=True, exist_ok=True)
    manifest = {
        "release_schema_version": "kg-release-manifest.v1",
        "release_id": release_id,
        "created_at": "2026-08-01T00:00:00+00:00",
        "producer": {"application": "knowledge-graph", "git_commit": "abc"},
        "mode": "full",
        "observation_window": {
            "start": "2026-07-01T00:00:00+00:00",
            "end": "2026-07-31T23:59:59+00:00",
        },
        "artifacts": [
            {
                "artifact_type": "published-jd-facts",
                "contract_version": "published-jd-fact.v3",
                "path": "facts.jsonl.gz",
                "record_count": len(facts),
            }
        ],
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    with gzip.open(target / "facts.jsonl.gz", "wt", encoding="utf-8") as handle:
        for fact in facts:
            handle.write(json.dumps(fact, ensure_ascii=False) + "\n")
    return release_dir


def test_trend_insight_bff_uses_exact_evidence_set_and_release_registry(
    tmp_path: Path,
) -> None:
    from app.api.dependencies.release_registry import get_release_registry
    from app.api.dependencies.trend_reports import get_trend_report_use_cases

    release_dir = _write_release_manifest(tmp_path, "REL-TEST-1")
    app.dependency_overrides[get_release_registry] = lambda: (
        ManifestReleaseRegistry(release_dir)
    )
    app.dependency_overrides[get_trend_report_use_cases] = (
        lambda: Namespace(get=lambda *args: _trend_record())
    )
    with SessionLocal() as db:
        for index, evidence_id in enumerate(("ev-1", "ev-2", "ev-3")):
            db.add(
                EvidenceSource(
                    id=evidence_id,
                    source_type=("boss", "liepin", "lagou")[index],
                    source_name=evidence_id,
                    title=evidence_id,
                    raw_text=evidence_id,
                    publish_date=date(2026, 7, index + 1),
                    credibility_score=0.9,
                    related_object_type="position",
                    related_object_id="pos-1",
                    created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
                    updated_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
                )
            )
        db.commit()
    token = _register_and_login("insight_release", "admin")
    response = client.get(
        "/api/v1/innovation/insights/trend/report-1?release_id=REL-TEST-1",
        headers=_headers(token),
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["release_refs"] == ["REL-TEST-1"]
    assert data["used_evidence_ids"] == ["ev-1", "ev-2"]
    assert [ref["evidence_id"] for ref in data["evidence_refs"]] == [
        "ev-1",
        "ev-2",
    ]
    assert data["effective_sample_size"] == pytest.approx(2.0, abs=1e-3)
    assert data["sensitivity_results"] == []
    assert "evidence_identity_incomplete" in data["limitations"]
    assert "sensitivity_pending_verification" in data["limitations"]


def test_trend_insight_bff_rejects_unknown_release(tmp_path: Path) -> None:
    from app.api.dependencies.release_registry import get_release_registry
    from app.api.dependencies.trend_reports import get_trend_report_use_cases

    app.dependency_overrides[get_release_registry] = lambda: (
        ManifestReleaseRegistry(tmp_path / "missing")
    )
    app.dependency_overrides[get_trend_report_use_cases] = (
        lambda: Namespace(get=lambda *args: _trend_record())
    )
    token = _register_and_login("insight_bad_release", "admin")
    response = client.get(
        "/api/v1/innovation/insights/trend/report-1?release_id=NOPE",
        headers=_headers(token),
    )
    assert response.status_code == 422


def _insert_versioned_trend_evidence() -> None:
    with SessionLocal() as db:
        for index in (1, 2):
            db.add(
                EvidenceSource(
                    id=f"ev-{index}",
                    source_type="jd",
                    source_name="Boss直聘",
                    source_platform="boss_zhipin",
                    title=f"ev-{index}",
                    raw_text=f"ev-{index}",
                    publish_date=date(2026, 7, index),
                    credibility_score=0.9,
                    related_object_type="position",
                    related_object_id="pos-1",
                    source_jd_id=f"jd-{index}",
                    source_fact_id=f"fact-{index}",
                    source_version="2026-07-10T00:00:00+00:00",
                    created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
                    updated_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
                )
            )
        db.commit()


def test_trend_insight_bff_verifies_exact_artifact_membership(tmp_path: Path) -> None:
    from app.api.dependencies.release_registry import get_release_registry
    from app.api.dependencies.trend_reports import get_trend_report_use_cases

    facts = tuple(
        _published_fact(
            source_jd_id=f"jd-{index}",
            source_fact_id=f"fact-{index}",
        )
        for index in (1, 2)
    )
    release_dir = _write_release_manifest(tmp_path, "REL-MEMBER", facts)
    app.dependency_overrides[get_release_registry] = lambda: ManifestReleaseRegistry(
        release_dir
    )
    app.dependency_overrides[get_trend_report_use_cases] = lambda: Namespace(
        get=lambda *args: _trend_record()
    )
    _insert_versioned_trend_evidence()
    token = _register_and_login("insight_member", "admin")
    response = client.get(
        "/api/v1/innovation/insights/trend/report-1?release_id=REL-MEMBER",
        headers=_headers(token),
    )
    assert response.status_code == 200
    assert "evidence_identity_incomplete" not in response.json()["data"]["limitations"]


def test_trend_insight_bff_rejects_window_only_membership(tmp_path: Path) -> None:
    from app.api.dependencies.release_registry import get_release_registry
    from app.api.dependencies.trend_reports import get_trend_report_use_cases

    release_dir = _write_release_manifest(
        tmp_path,
        "REL-NONMEMBER",
        (_published_fact(source_jd_id="jd-1", source_fact_id="fact-1"),),
    )
    app.dependency_overrides[get_release_registry] = lambda: ManifestReleaseRegistry(
        release_dir
    )
    app.dependency_overrides[get_trend_report_use_cases] = lambda: Namespace(
        get=lambda *args: _trend_record()
    )
    _insert_versioned_trend_evidence()
    token = _register_and_login("insight_nonmember", "admin")
    response = client.get(
        "/api/v1/innovation/insights/trend/report-1?release_id=REL-NONMEMBER",
        headers=_headers(token),
    )
    assert response.status_code == 409
    assert "artifact membership" in response.json()["message"]


def _insert_jd_evidence(evidence_ids, jd_ids) -> None:
    with SessionLocal() as db:
        for index, (evidence_id, jd_id) in enumerate(
            zip(evidence_ids, jd_ids)
        ):
            db.add(
                EvidenceSource(
                    id=evidence_id,
                    source_type=("boss", "liepin", "lagou")[index % 3],
                    source_name=evidence_id,
                    title=evidence_id,
                    raw_text=evidence_id,
                    publish_date=date(2026, 7, index + 1),
                    credibility_score=0.9,
                    related_object_type="source_jd",
                    related_object_id=jd_id,
                    created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
                    updated_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
                )
            )
        db.commit()


def test_emerging_insight_bff_resolves_jd_ids_to_governance_evidence(
    tmp_path: Path,
) -> None:
    from app.api.dependencies.release_registry import get_release_registry
    from app.api.dependencies.use_cases import get_emerging_position_handlers

    release_dir = _write_release_manifest(tmp_path, "REL-EMERGE-1")
    app.dependency_overrides[get_release_registry] = lambda: (
        ManifestReleaseRegistry(release_dir)
    )
    candidate = EmergingCandidate.create(
        candidate_id="emerging-1",
        cluster_id="cluster-1",
        position_name="AI Prompt Engineer",
        core_responsibilities=("design prompts",),
        required_skills=({"skill_id": "s1", "level": "required"},),
        bonus_skills=(),
        industry_scenarios=("internet",),
        germination_score=0.9,
        score_dimensions={"novelty": 0.8},
        evidence_jd_ids=("jd-1", "jd-2"),
        status="published",
    )
    handlers = Namespace(
        query=Namespace(
            get=lambda *args: EmergingRecord(
                candidate=candidate, created_at=None, updated_at=None
            )
        )
    )
    app.dependency_overrides[get_emerging_position_handlers] = (
        lambda: handlers
    )
    _insert_jd_evidence(("gov-1", "gov-2"), ("jd-1", "jd-2"))
    token = _register_and_login("insight_emerge_resolver", "admin")
    response = client.get(
        "/api/v1/innovation/insights/emerging/emerging-1"
        "?release_id=REL-EMERGE-1",
        headers=_headers(token),
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["used_evidence_ids"] == ["gov-1", "gov-2"]
    assert [ref["evidence_id"] for ref in data["evidence_refs"]] == [
        "gov-1",
        "gov-2",
    ]
    assert data["effective_sample_size"] == pytest.approx(2.0, abs=1e-3)


def test_evolution_insight_bff_resolves_jd_ids_to_governance_evidence(
    tmp_path: Path,
) -> None:
    from app.api.dependencies.knowledge_graph import get_knowledge_graph_handlers
    from app.api.dependencies.release_registry import get_release_registry

    release_dir = _write_release_manifest(tmp_path, "REL-EVOLVE-1")
    app.dependency_overrides[get_release_registry] = lambda: (
        ManifestReleaseRegistry(release_dir)
    )
    event = {
        "event_id": "evt-1-2-skill_emergence-001",
        "event_type": "skill_emergence",
        "position_id": "pos-1",
        "from_version": 1,
        "to_version": 2,
        "source_entities": [],
        "target_entities": [{"canonical_name": "Python", "skill_id": "s1"}],
        "evidence": {
            "source": "graph_version_snapshot_diff",
            "evidence_refs": ["jd-1", "jd-2"],
        },
        "detector_version": "position-evolution-events.v1",
        "config_version": "config.v1",
    }
    handlers = Namespace(
        portal=lambda *args: Namespace(
            result=freeze_json_object(event),
            upstream=Namespace(trace_id="trace-1"),
        )
    )
    app.dependency_overrides[get_knowledge_graph_handlers] = lambda: handlers
    _insert_jd_evidence(("gov-1", "gov-2"), ("jd-1", "jd-2"))
    token = _register_and_login("insight_evolve_resolver", "admin")
    response = client.get(
        "/api/v1/innovation/insights/evolution/pos-1/"
        "evt-1-2-skill_emergence-001?release_id=REL-EVOLVE-1",
        headers=_headers(token),
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["used_evidence_ids"] == ["gov-1", "gov-2"]
    assert [ref["evidence_id"] for ref in data["evidence_refs"]] == [
        "gov-1",
        "gov-2",
    ]
    assert data["effective_sample_size"] == pytest.approx(2.0, abs=1e-3)


def test_matching_scenario_is_persisted_and_reads_review_decision() -> None:
    from app.api.dependencies.matching import get_matching_use_cases
    from app.domain.accounts import AccountActor

    result = {
        "scenario_id": "scenario-persist-1",
        "baseline_evaluation_id": "eval-1",
        "baseline_score": 60.0,
        "scenario_score": 75.0,
        "score_delta": 15.0,
        "scenario_hard_gate_status": "passed",
        "generation_status": "completed",
        "target_reachable": True,
        "algorithm_version": "counterfactual-profile.v2",
        "scoring_config_version": "scoring-config.v1",
        "evidence_refs": [
            {
                "evidence_id": "ev-1",
                "source_object_type": "matching_evidence",
                "source_object_id": "ev-1",
                "source_document_id": "doc-1",
                "source_version": "v1",
            }
        ],
    }
    use_cases = Namespace(what_if=lambda *args, **kwargs: result)
    app.dependency_overrides[get_matching_use_cases] = lambda: use_cases
    token = _register_and_login("insight_scenario", "admin")
    response = client.post(
        "/api/v1/innovation/insights/matching/eval-1/what-if",
        headers=_headers(token),
        json={
            "actions": [
                {
                    "action_id": "a1",
                    "action_type": "add_skill",
                    "skill_id": "s1",
                    "estimated_hours": 10,
                }
            ]
        },
    )
    assert response.status_code == 200
    assert "scenario-persist-1" in response.json()["data"]["claim"]

    container = app.state.container
    user_id = create_internal_user("insight_scenario", "admin")
    actor = AccountActor(user_id, "admin")
    tasks = container.governance.reviews.list(actor)
    matching_tasks = [
        task
        for task in tasks
        if task.object_type == "matching_scenario"
        and task.object_id == "scenario-persist-1"
    ]
    assert matching_tasks
    task = matching_tasks[0]
    container.governance.reviews.transition(
        actor, task.task_id, action="claim"
    )
    container.governance.reviews.transition(
        actor, task.task_id, action="approve", comment="ok"
    )

    card = client.get(
        "/api/v1/innovation/insights/matching/scenario-persist-1",
        headers=_headers(token),
    )
    assert card.status_code == 200
    data = card.json()["data"]
    assert data["claim_type"] == "matching_what_if"
    assert data["algorithm_version"] == "counterfactual-profile.v2"
    assert data["human_decision"]["decision"] == "approved"
    assert data["human_decision"]["bound_object_type"] == "matching_scenario"
    assert data["human_decision"]["bound_object_id"] == "scenario-persist-1"
    assert data["next_action"] == "user_action"


def test_personal_user_insight_evidence_resolves_without_governance_role(
    tmp_path: Path,
) -> None:
    from app.api.dependencies.release_registry import get_release_registry
    from app.api.dependencies.trend_reports import get_trend_report_use_cases

    release_dir = _write_release_manifest(tmp_path, "REL-PERSONAL-1")
    app.dependency_overrides[get_release_registry] = lambda: (
        ManifestReleaseRegistry(release_dir)
    )
    app.dependency_overrides[get_trend_report_use_cases] = (
        lambda: Namespace(get=lambda *args: _trend_record())
    )
    _insert_jd_evidence(("ev-1", "ev-2"), ("jd-1", "jd-2"))
    token = _register_and_login("insight_personal", "personal_user")
    response = client.get(
        "/api/v1/innovation/insights/trend/report-1?release_id=REL-PERSONAL-1",
        headers=_headers(token),
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["used_evidence_ids"] == ["ev-1", "ev-2"]
    assert data["effective_sample_size"] == pytest.approx(2.0, abs=1e-3)
    assert data["release_refs"] == ["REL-PERSONAL-1"]


def test_personal_user_what_if_review_chain_uses_internal_review() -> None:
    from app.api.dependencies.matching import get_matching_use_cases
    from app.domain.accounts import AccountActor

    result = {
        "scenario_id": "scenario-user-1",
        "baseline_evaluation_id": "eval-user-1",
        "baseline_score": 55.0,
        "scenario_score": 70.0,
        "score_delta": 15.0,
        "scenario_hard_gate_status": "passed",
        "generation_status": "completed",
        "target_reachable": True,
        "algorithm_version": "counterfactual-profile.v2",
        "scoring_config_version": "scoring-config.v1",
        "evidence_refs": [],
    }
    app.dependency_overrides[get_matching_use_cases] = (
        lambda: Namespace(what_if=lambda *args, **kwargs: result)
    )
    token = _register_and_login("scenario_user", "personal_user")
    response = client.post(
        "/api/v1/innovation/insights/matching/eval-user-1/what-if",
        headers=_headers(token),
        json={
            "actions": [
                {
                    "action_id": "a1",
                    "action_type": "add_skill",
                    "skill_id": "s1",
                    "estimated_hours": 10,
                }
            ]
        },
    )
    assert response.status_code == 200

    reviewer_id = create_internal_user("scenario_reviewer", "admin")
    reviewer = AccountActor(reviewer_id, "admin")
    container = app.state.container
    tasks = container.governance.reviews.list(reviewer)
    scenario_tasks = [
        task
        for task in tasks
        if task.object_type == "matching_scenario"
        and task.object_id == "scenario-user-1"
    ]
    assert scenario_tasks
    container.governance.reviews.transition(
        reviewer,
        scenario_tasks[0].task_id,
        action="claim",
    )
    container.governance.reviews.transition(
        reviewer,
        scenario_tasks[0].task_id,
        action="approve",
        comment="ok",
    )

    card = client.get(
        "/api/v1/innovation/insights/matching/scenario-user-1",
        headers=_headers(token),
    )
    assert card.status_code == 200
    data = card.json()["data"]
    assert data["human_decision"]["decision"] == "approved"
    assert data["human_decision"]["bound_object_id"] == "scenario-user-1"


def test_matching_scenario_ownership_gate_blocks_other_users() -> None:
    from app.api.dependencies.matching import get_matching_use_cases

    result = {
        "scenario_id": "scenario-owned-1",
        "baseline_evaluation_id": "eval-owned-1",
        "baseline_score": 55.0,
        "scenario_score": 70.0,
        "score_delta": 15.0,
        "scenario_hard_gate_status": "passed",
        "generation_status": "completed",
        "target_reachable": True,
        "algorithm_version": "counterfactual-profile.v2",
        "scoring_config_version": "scoring-config.v1",
        "evidence_refs": [],
    }
    app.dependency_overrides[get_matching_use_cases] = (
        lambda: Namespace(what_if=lambda *args, **kwargs: result)
    )
    owner_token = _register_and_login("scenario_owner", "personal_user")
    created = client.post(
        "/api/v1/innovation/insights/matching/eval-owned-1/what-if",
        headers=_headers(owner_token),
        json={
            "actions": [
                {
                    "action_id": "a1",
                    "action_type": "add_skill",
                    "skill_id": "s1",
                    "estimated_hours": 10,
                }
            ]
        },
    )
    assert created.status_code == 200

    owner_card = client.get(
        "/api/v1/innovation/insights/matching/scenario-owned-1",
        headers=_headers(owner_token),
    )
    assert owner_card.status_code == 200

    intruder_token = _register_and_login("scenario_intruder", "personal_user")
    intruder_card = client.get(
        "/api/v1/innovation/insights/matching/scenario-owned-1",
        headers=_headers(intruder_token),
    )
    assert intruder_card.status_code == 404

    reviewer_token = _register_and_login(
        "scenario_reviewer_read", "reviewer"
    )
    reviewer_card = client.get(
        "/api/v1/innovation/insights/matching/scenario-owned-1",
        headers=_headers(reviewer_token),
    )
    assert reviewer_card.status_code == 200


# ---- TEMP-LAG-01: real SourceJDVersion crawl_time wiring -------------------


class _FakeSourceJDs:
    def __init__(self, versions_by_id):
        self._versions_by_id = versions_by_id

    def list_versions(self, source_jd_id):
        versions = tuple(
            version
            for version in self._versions_by_id.values()
            if version.source_jd_id == source_jd_id
        )
        if not versions:
            raise SourceJDNotFound(source_jd_id)
        return versions

    def get_version(self, version_id):
        if version_id not in self._versions_by_id:
            raise SourceJDNotFound(version_id)
        return self._versions_by_id[version_id]


class _FakeReleaseRegistry:
    def __init__(self, release_id="rel-crawler-1"):
        self._release_id = release_id

    def resolve(self, release_id):
        if release_id != self._release_id:
            raise ReleaseNotFound(release_id)
        return Namespace(
            observation_window_end=datetime(2026, 8, 12, tzinfo=timezone.utc)
        )

    def evidence_within_release(self, identity, publish_date):
        return True

    def evidence_belongs_to_release(
        self, identity, *, source_jd_id, source_fact_id, source_version
    ):
        return True


def _governance_with_source(
    evidence_id,
    *,
    source_jd_id="jd-1",
    source_version="fact-v1",
    source_fact_id="fact-1",
    source_jd_version_id=None,
    created_at=None,
    publish_date=None,
):
    from app.contexts.governance_feedback import (
        EvidenceRecord as GovernanceEvidenceRecord,
    )

    return GovernanceEvidenceRecord(
        evidence_id=evidence_id,
        source_type="source_jd",
        source_name=None,
        title="JD 数据时滞接线测试",
        url=None,
        raw_text="真实业务证据文本",
        publish_date=publish_date,
        credibility_score=0.9,
        related_object_type="source_jd",
        related_object_id=source_jd_id,
        source_jd_id=source_jd_id,
        source_fact_id=source_fact_id,
        source_version=source_version,
        source_jd_version_id=source_jd_version_id,
        created_at=created_at,
        updated_at=created_at,
        source_platform="boss_zhipin",
        enterprise_id="ent-1",
    )


def _crawler_version(
    version_id,
    crawl_time,
    *,
    source_version="crawler-v1",
    source_jd_id="jd-1",
):
    from app.contexts.source_jds import SourceJDVersionRecord

    return SourceJDVersionRecord(
        id=version_id,
        source_jd_id=source_jd_id,
        source_version=source_version,
        schema_version="crawler-jd.v1",
        raw_text="...",
        content_hash="hash",
        raw_payload={},
        raw_html=None,
        source_url="https://example.com/jd",
        crawl_time=crawl_time,
        job_title_raw=None,
        company_name_raw=None,
        region_raw=None,
        publish_time_raw=None,
        text_canonicalization_version="v1",
        created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )


def test_release_evidence_wires_source_jd_version_id_to_crawler_acquired() -> None:
    from app.api.v1.insights import _release_evidence

    crawl = datetime(2026, 7, 9, tzinfo=timezone.utc)
    # Governance ``source_version`` is the SOURCE FACT version (fact-v1); the
    # crawler SourceJDVersion uses a different source_version (crawler-v1).
    # Lineage must go through source_jd_version_id, never through source_version.
    row = _governance_with_source(
        "g-1",
        source_version="fact-v1",
        source_jd_version_id="ver-1",
        publish_date=date(2026, 7, 5),
        created_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
    )
    container = Namespace(
        source_jds=_FakeSourceJDs(
            {"ver-1": _crawler_version("ver-1", crawl, source_version="crawler-v1")}
        )
    )
    registry = _FakeReleaseRegistry("rel-crawler-1")
    summary, _certificate = _release_evidence(
        container,
        registry,
        "rel-crawler-1",
        subject_ref="BACKEND_ENGINEER",
        rows=(row,),
        conclusion=None,
    )
    assert summary is not None
    assert summary.temporal_certificate is not None
    # the real SourceJDVersion.crawl_time reached the EvidenceRecord as
    # collection_time_basis == crawler_acquired (counted by the certificate),
    # even though created_at (pipeline) was also present.
    assert summary.temporal_certificate.crawler_acquired_count == 1
    assert summary.temporal_certificate.pipeline_observed_count == 0


def test_release_evidence_mismatched_source_jd_version_id_is_not_crawler() -> None:
    from app.api.v1.insights import _release_evidence

    row = _governance_with_source(
        "g-mismatch",
        source_jd_id="jd-1",
        source_version="fact-v1",
        # version id belongs to a DIFFERENT SourceJD (jd-other)
        source_jd_version_id="ver-other",
        publish_date=date(2026, 7, 5),
        created_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
    )
    container = Namespace(
        source_jds=_FakeSourceJDs(
            {
                "ver-other": _crawler_version(
                    "ver-other",
                    datetime(2026, 7, 8, tzinfo=timezone.utc),
                    source_jd_id="jd-other",
                )
            }
        )
    )
    registry = _FakeReleaseRegistry("rel-crawler-mismatch")
    summary, _certificate = _release_evidence(
        container,
        registry,
        "rel-crawler-mismatch",
        subject_ref="BACKEND_ENGINEER",
        rows=(row,),
        conclusion=None,
    )
    assert summary is not None
    assert summary.temporal_certificate is not None
    assert summary.temporal_certificate.crawler_acquired_count == 0
    assert summary.temporal_certificate.pipeline_observed_count == 1


def test_release_evidence_without_crawl_time_falls_back_to_pipeline() -> None:
    from app.api.v1.insights import _release_evidence

    row = _governance_with_source(
        "g-2",
        source_version="fact-v1",
        source_jd_version_id="ver-1",
        publish_date=date(2026, 7, 5),
        created_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
    )
    # the version exists but carries no crawl_time -> must NOT be treated as a
    # crawler; created_at maps to pipeline_observed.
    container = Namespace(
        source_jds=_FakeSourceJDs({"ver-1": _crawler_version("ver-1", None)})
    )
    registry = _FakeReleaseRegistry("rel-crawler-2")
    summary, _certificate = _release_evidence(
        container,
        registry,
        "rel-crawler-2",
        subject_ref="BACKEND_ENGINEER",
        rows=(row,),
        conclusion=None,
    )
    assert summary is not None
    assert summary.temporal_certificate is not None
    assert summary.temporal_certificate.crawler_acquired_count == 0
    assert summary.temporal_certificate.pipeline_observed_count == 1
