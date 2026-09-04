from types import SimpleNamespace
import hashlib

from fastapi.testclient import TestClient

from app.auth import hash_password
from app.config import Settings
from app.database import Base
from app.domain.policies import (
    calculate_trend_score,
    version_diff,
)
from app.main import create_app
from app.models import (
    PositionCategory,
    Skill,
    SkillCategory,
    StandardPosition,
    User,
)
from tests.factories import approve_build_tasks


TREND_POSITION = "POS_TREND"
RISING_SKILL = "SKILL_TREND_RISING"
FALLING_SKILL = "SKILL_TREND_FALLING"
STABLE_SKILL = "SKILL_TREND_STABLE"


def _auth_headers(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/auth/token", json={"username": "trend-admin", "password": "secret"}
    )
    assert response.status_code == 200
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _seed_catalog_and_user(application):
    Base.metadata.create_all(application.state.database.engine)
    with application.state.database.session_factory() as db:
        db.add_all(
            [
                User(
                    username="trend-admin",
                    password_hash=hash_password("secret"),
                    role="admin",
                ),
                PositionCategory(code="TREND", name="趋势岗位"),
                SkillCategory(code="TREND_SKILL", name="趋势技能"),
                StandardPosition(
                    position_id=TREND_POSITION,
                    position_code=TREND_POSITION,
                    name="趋势分析岗位",
                    category_code="TREND",
                    taxonomy_version="position-taxonomy.v3.0.0",
                    sample_support_status="sufficient",
                ),
                Skill(
                    skill_id=RISING_SKILL,
                    canonical_name="Trend Rising",
                    category_code="TREND_SKILL",
                ),
                Skill(
                    skill_id=FALLING_SKILL,
                    canonical_name="Trend Falling",
                    category_code="TREND_SKILL",
                ),
                Skill(
                    skill_id=STABLE_SKILL,
                    canonical_name="Trend Stable",
                    category_code="TREND_SKILL",
                ),
            ]
        )
        db.commit()


def _configure_frequency_only_algorithm(client: TestClient, headers: dict):
    payload = {
        "weight_coefficients": {
            "weighted_frequency": 0.0,
            "support_ratio": 1.0,
            "modality_strength": 0.0,
            "source_diversity": 0.0,
            "enterprise_coverage": 0.0,
            "freshness_score": 0.0,
            "trusted_evidence_ratio": 0.0,
        },
        "confidence_coefficients": {
            "weighted_frequency": 0.0,
            "support_sufficiency": 1.0,
            "trusted_evidence_ratio": 0.0,
            "source_diversity": 0.0,
        },
    }
    response = client.put(
        "/api/v1/algorithm-config",
        json={"version": "trend-frequency-only-v1", "payload": payload},
        headers=headers,
    )
    assert response.status_code == 200


def _post_document(client: TestClient, headers: dict, doc_id: str, skills: list[str]):
    title = "趋势分析岗位"
    unique_body = " ".join(
        hashlib.sha256(f"{doc_id}-{index}".encode()).hexdigest()
        for index in range(40)
    )
    lines = [title, f"业务样本 {doc_id} {unique_body}"]
    skill_quotes = {
        RISING_SKILL: f"TrendRise token {doc_id}",
        FALLING_SKILL: f"TrendFall token {doc_id}",
        STABLE_SKILL: f"TrendStable token {doc_id}",
    }
    lines.extend(skill_quotes[skill_id] for skill_id in skills)
    raw_text = "\n".join(lines)
    response = client.post(
        "/api/v1/jds",
        json={
            "document_id": doc_id,
            "raw_text": raw_text,
            "source_type": "real_acceptance",
            "source_name": f"source-{doc_id}",
            "enterprise_name": f"enterprise-{doc_id}",
            "source_credibility": 1.0,
            "is_synthetic": False,
        },
        headers=headers,
    )
    assert response.status_code == 200

    name_by_skill = {
        RISING_SKILL: "TrendRise",
        FALLING_SKILL: "TrendFall",
        STABLE_SKILL: "TrendStable",
    }
    requirements = []
    normalized_requirements = []
    for index, skill_id in enumerate(skills, start=1):
        quote = skill_quotes[skill_id]
        requirement_id = f"skill-{index}"
        source_name = name_by_skill[skill_id]
        requirements.append(
            {
                "requirement_id": requirement_id,
                "kind": "skill",
                "modality": "required",
                "evidence": {"source_id": doc_id, "quote": quote},
                "items": [{"name": source_name, "item_type": "technology"}],
            }
        )
        normalized_requirements.append(
            {
                "requirement_id": requirement_id,
                "kind": "skill",
                "normalized_skills": [
                    {
                        "source_name": source_name,
                        "skill_id": skill_id,
                        "canonical_name": source_name,
                        "category_code": "TREND_SKILL",
                        "subcategory_code": None,
                        "resolution_status": "resolved",
                        "resolution_source": "same_id",
                    }
                ],
            }
        )

    extraction = {
        "schema_version": "v2",
        "document_id": doc_id,
        "job_title": {"text": title, "evidence": {"source_id": doc_id, "quote": title}},
        "responsibilities": [],
        "requirements": requirements,
        "company_facts": [],
        "employment_facts": [],
    }
    response = client.post(
        f"/api/v1/jds/{doc_id}/extraction-result/import",
        json=extraction,
        headers=headers,
    )
    assert response.status_code == 200
    assert client.post(
        f"/api/v1/jds/{doc_id}/extraction-result/align", headers=headers
    ).status_code == 200

    normalized = {
        "schema_version": "v2",
        "document_id": doc_id,
        "job_classification": {
            "schema_version": "job-position-classification.v3",
            "taxonomy_version": "position-taxonomy.v3.0.0",
            "source_title": title,
            "position_code": TREND_POSITION,
            "position_name": title,
            "family_code": "TREND",
            "family_name": "趋势岗位",
            "candidate_positions": [
                {"position_code": TREND_POSITION, "score": 0.95}
            ],
            "career_level": "mid",
            "leadership_scope": "none",
            "technology_focus_codes": [],
            "industry_context_codes": [],
            "observed_skill_domain_codes": ["software_engineering"],
            "confidence": 0.95,
            "classification_status": "resolved",
            "review_reason_codes": [],
            "evidence_refs": (
                [item["requirement_id"] for item in requirements] or ["job_title"]
            ),
            "classification_policy_version": "position-classifier.v3.0",
        },
        "normalized_requirements": normalized_requirements,
        "salary": None,
        "unresolved_items": [],
    }
    response = client.post(
        f"/api/v1/jds/{doc_id}/normalized-result/import",
        json=normalized,
        headers=headers,
    )
    assert response.status_code == 200
    assert client.post(
        f"/api/v1/jds/{doc_id}/duplicate-check", json={}, headers=headers
    ).status_code == 200


def _post_batch(client: TestClient, headers: dict, start: int, total: int, plan):
    for offset in range(total):
        doc_number = start + offset
        _post_document(client, headers, f"TREND_DOC_{doc_number:02d}", plan(offset))


def _publish_current_graph(client: TestClient, headers: dict) -> dict:
    response = client.post(
        f"/api/v1/positions/{TREND_POSITION}/graph/build",
        json={"minimum_valid_samples": 3},
        headers=headers,
    )
    assert response.status_code == 200
    build = response.json()["data"]
    approve_build_tasks(client, build["build_run_id"], headers)
    response = client.post(
        f'/api/v1/graph/build-runs/{build["build_run_id"]}/publish',
        json={},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _weights_by_skill(graph: dict) -> dict[str, float]:
    return {
        relation["skill_id"]: relation["weight"]
        for relation in graph["skill_relations"]
    }


def _trends_by_skill(graph: dict) -> dict[str, float | None]:
    return {
        relation["skill_id"]: relation["trend_score"]
        for relation in graph["skill_relations"]
    }



def snapshot(score: float, *, skill_id: str = "SKILL_PYTHON", name: str = "Python"):
    return {
        "sample_stats": {"included_samples": 3},
        "skill_relations": [
            {"skill_id": skill_id, "canonical_name": name, "weight": score}
        ],
    }


def test_trend_uses_skill_relations_from_previous_snapshot():
    assert calculate_trend_score(snapshot(0.40), "SKILL_PYTHON", 0.65, 3) == 0.25


def test_positive_trend_when_score_increases():
    assert calculate_trend_score(snapshot(0.40), "SKILL_PYTHON", 0.65, 3) == 0.25


def test_negative_trend_when_score_decreases():
    assert calculate_trend_score(snapshot(0.65), "SKILL_PYTHON", 0.40, 3) == -0.25


def test_zero_trend_when_score_unchanged():
    assert calculate_trend_score(snapshot(0.40), "SKILL_PYTHON", 0.40, 3) == 0.0


def test_trend_none_without_previous_version():
    assert calculate_trend_score(None, "SKILL_PYTHON", 0.65, 3) is None


def test_legacy_skills_snapshot_is_read_by_compatibility_mapper():
    legacy = {
        "sample_stats": {"included_samples": 3},
        "skills": [{"skill_id": "SKILL_PYTHON", "weight": 0.40}],
    }
    assert calculate_trend_score(legacy, "SKILL_PYTHON", 0.65, 3) == 0.25


def test_trend_matches_by_skill_id_not_display_name():
    previous = snapshot(0.40, name="Old Python Name")
    assert calculate_trend_score(previous, "SKILL_PYTHON", 0.65, 3) == 0.25
    assert calculate_trend_score(previous, "DIFFERENT_ID", 0.65, 3) == 0.65


def test_added_removed_and_missing_skill_trend_semantics_are_explicit():
    assert calculate_trend_score(snapshot(0.40), "NEW_SKILL", 0.35, 3) == 0.35
    assert calculate_trend_score(None, "MISSING_PREVIOUS_VERSION", 0.35, 3) is None
    before = SimpleNamespace(
        snapshot={
            "sample_stats": {"included_samples": 3},
            "skill_relations": [
                {"skill_id": "REMOVED_SKILL", "weight": 0.6},
                {"skill_id": "UNCHANGED_SKILL", "weight": 0.5},
            ],
        }
    )
    after = SimpleNamespace(
        snapshot={
            "sample_stats": {"included_samples": 3},
            "skill_relations": [
                {"skill_id": "UNCHANGED_SKILL", "weight": 0.5},
                {"skill_id": "ADDED_SKILL", "weight": 0.4, "trend_score": 0.4},
            ],
        }
    )
    diff = version_diff(before.snapshot, after.snapshot)
    assert [item["skill_id"] for item in diff["removed"]] == ["REMOVED_SKILL"]
    assert [item["skill_id"] for item in diff["added"]] == ["ADDED_SKILL"]
    assert diff["changed"] == []


def test_trend_none_when_either_version_has_too_few_samples():
    previous = snapshot(0.40)
    previous["sample_stats"]["included_samples"] = 2
    assert calculate_trend_score(previous, "SKILL_PYTHON", 0.65, 3) is None
    assert calculate_trend_score(snapshot(0.40), "SKILL_PYTHON", 0.65, 2) is None


def test_real_api_published_v1_v2_trends_are_adjacent_and_persistent(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'trend.db').as_posix()}"
    runtime = Settings(
        database_url=database_url,
        jwt_secret_key="trend-test-secret-with-at-least-32-characters",
        build_jobs_inline=True,
    )
    application = create_app(runtime)
    _seed_catalog_and_user(application)

    with TestClient(application) as client:
        headers = _auth_headers(client)
        _configure_frequency_only_algorithm(client, headers)

        def v1_plan(index: int) -> list[str]:
            skills = []
            if index < 8:
                skills.append(RISING_SKILL)
            if index < 14:
                skills.append(FALLING_SKILL)
            if index < 10:
                skills.append(STABLE_SKILL)
            return skills

        _post_batch(client, headers, start=1, total=20, plan=v1_plan)
        v1 = _publish_current_graph(client, headers)
        v1_graph = client.get(
            f"/api/v1/positions/{TREND_POSITION}/graph"
        ).json()["data"]
        assert _weights_by_skill(v1_graph) == {
            RISING_SKILL: 0.4,
            FALLING_SKILL: 0.7,
            STABLE_SKILL: 0.5,
        }
        assert _trends_by_skill(v1_graph) == {
            RISING_SKILL: None,
            FALLING_SKILL: None,
            STABLE_SKILL: None,
        }

        def v2_plan(index: int) -> list[str]:
            skills = []
            if index < 18:
                skills.append(RISING_SKILL)
            if index < 6:
                skills.append(FALLING_SKILL)
            if index < 10:
                skills.append(STABLE_SKILL)
            return skills

        _post_batch(client, headers, start=21, total=20, plan=v2_plan)
        v2 = _publish_current_graph(client, headers)
        assert v1["version_number"] == 1
        assert v2["version_number"] == 2
        versions = client.get(
            f"/api/v1/positions/{TREND_POSITION}/graph/versions"
        ).json()["data"]
        assert [item["id"] for item in versions] == [v1["version_id"], v2["version_id"]]

        v2_graph = client.get(
            f"/api/v1/positions/{TREND_POSITION}/graph"
        ).json()["data"]
        assert _weights_by_skill(v2_graph) == {
            RISING_SKILL: 0.65,
            FALLING_SKILL: 0.5,
            STABLE_SKILL: 0.5,
        }
        assert _trends_by_skill(v2_graph) == {
            RISING_SKILL: 0.25,
            FALLING_SKILL: -0.2,
            STABLE_SKILL: 0.0,
        }
        diff = client.get(
            f"/api/v1/positions/{TREND_POSITION}/graph/versions/diff"
            f"?from_version_id={v1['version_id']}&to_version_id={v2['version_id']}"
        ).json()["data"]
        changed = {item["skill_id"]: item for item in diff["changed"]}
        assert set(changed) == {RISING_SKILL, FALLING_SKILL, STABLE_SKILL}
        assert changed[RISING_SKILL]["before"]["weight"] == 0.4
        assert changed[RISING_SKILL]["after"]["weight"] == 0.65
        assert changed[FALLING_SKILL]["before"]["weight"] == 0.7
        assert changed[FALLING_SKILL]["after"]["weight"] == 0.5
        assert changed[STABLE_SKILL]["before"]["weight"] == 0.5
        assert changed[STABLE_SKILL]["after"]["weight"] == 0.5
        assert changed[STABLE_SKILL]["after"]["trend_score"] == 0.0

    restarted = create_app(runtime)
    with TestClient(restarted) as client:
        persisted = client.get(
            f"/api/v1/positions/{TREND_POSITION}/graph"
        ).json()["data"]
        assert _weights_by_skill(persisted)[RISING_SKILL] == 0.65
        assert _trends_by_skill(persisted) == {
            RISING_SKILL: 0.25,
            FALLING_SKILL: -0.2,
            STABLE_SKILL: 0.0,
        }

    application.state.database.engine.dispose()
    restarted.state.database.engine.dispose()
