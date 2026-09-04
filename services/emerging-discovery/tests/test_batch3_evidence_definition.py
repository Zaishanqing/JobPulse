from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient

from app.domain.definition_generation import generate_evidence_definition
from app.domain.discovery import (
    AlgorithmCluster,
    JDSnapshot,
    JDStructuredData,
    SkillReference,
)
from app.domain.values import FrozenDict, thaw
from app.main import app
from tests.test_algorithm_comparison import _payload


client = TestClient(app)
HEADERS = {"Authorization": "Bearer development-emerging-discovery-token-change-me"}


def _member(
    index: int,
    *,
    title: str = "RAG 应用工程师",
    responsibility: str = "负责 RAG 应用开发与优化",
    required: tuple[str, ...] = ("Python", "RAG"),
    bonus: tuple[str, ...] = (),
    scenario: str | None = "智能客服",
) -> JDSnapshot:
    return JDSnapshot(
        jd_id=f"definition-jd-{index}",
        source_fact_id=f"definition-fact-{index}",
        source_fact_version="1",
        schema_version="v2",
        review_status="published",
        consumption_path="published",
        title=title,
        source_name=f"source-{index % 2}",
        publish_date=date(2026, index + 1, 1),
        structured_data=JDStructuredData(
            responsibilities=(responsibility,) if responsibility else (),
            required_skills=tuple(SkillReference(raw_skill=item) for item in required),
            bonus_skills=tuple(SkillReference(raw_skill=item) for item in bonus),
            business_scenarios=(scenario,) if scenario else (),
            industry="人工智能" if scenario else None,
            extensions=FrozenDict({
                "company_name": f"company-{index % 3}",
                "source_platform": f"platform-{index % 2}",
            }),
        ),
    )


def _cluster(members: tuple[JDSnapshot, ...]) -> AlgorithmCluster:
    return AlgorithmCluster(
        key="definition-cluster",
        cluster_name="definition",
        members=members,
        core_skills=("python", "rag"),
        stability_score=0.9,
        centroid=(1.0,),
        algorithm_version="test",
        similarity_threshold=0.5,
        random_seed=42,
    )


def test_definition_aggregates_responsibilities_and_separates_skills():
    members = (
        _member(0, bonus=("LangChain",)),
        _member(1, responsibility="负责RAG应用的开发与优化", bonus=("LangChain",)),
        _member(2),
        _member(3, required=("Python",)),
    )
    definition = generate_evidence_definition(_cluster(members))
    evidence = thaw(definition.field_evidence)

    assert definition.position_name == "RAG 应用工程师"
    assert len(definition.core_responsibilities) == 1
    assert {item.normalized_skill_id for item in definition.required_skills} == {
        "python",
        "rag",
    }
    assert {item.normalized_skill_id for item in definition.bonus_skills} == {
        "langchain"
    }
    assert set(definition.industry_scenarios) == {"智能客服", "人工智能"}
    assert evidence["core_responsibilities"]["support_jd_count"] == 4
    assert evidence["bonus_skills"]["items"][0]["support_source_count"] == 2


def test_every_definition_field_has_bound_evidence_counts_and_confidence():
    definition = generate_evidence_definition(_cluster((_member(0), _member(1))))
    fields = thaw(definition.field_evidence)
    for value in fields.values():
        assert set(value) >= {
            "content",
            "evidence_ids",
            "support_jd_count",
            "support_enterprise_count",
            "support_source_count",
            "confidence",
            "generation_method",
            "missing_reason",
            "suggested_data",
        }
        assert 0.0 <= value["confidence"] <= 1.0
        assert value["generation_method"] in {
            "rule_based_candidate",
            "evidence_extractive",
            "standard_position_skill_difference",
            "member_enterprise_distribution",
            "window_member_count",
        }
    assert all(
        evidence_id.startswith("definition-fact-")
        for evidence_id in fields["position_name"]["evidence_ids"]
    )


def test_missing_facts_remain_empty_with_reason_and_suggestion():
    member = _member(
        0,
        title="",
        responsibility="",
        required=(),
        bonus=(),
        scenario=None,
    )
    definition = generate_evidence_definition(_cluster((member,)))
    fields = thaw(definition.field_evidence)
    assert definition.position_name == ""
    assert definition.core_responsibilities == ()
    assert definition.required_skills == ()
    assert definition.bonus_skills == ()
    assert definition.industry_scenarios == ()
    publish_claim_fields = (
        "position_name",
        "position_summary",
        "core_responsibilities",
        "required_skills",
        "distinguishing_features",
    )
    assert all(fields[name]["missing_reason"] for name in publish_claim_fields)
    assert all(fields[name]["confidence"] == 0.0 for name in publish_claim_fields)


def test_definition_evidence_is_returned_by_create_and_query_api():
    payload = _payload()
    payload["request_id"] = "batch3-definition-api"
    created = client.post("/api/v1/discovery-runs", json=payload, headers=HEADERS)
    assert created.status_code == 201
    created_data = created.json()["data"]
    queried = client.get(
        f"/api/v1/discovery-runs/{created_data['run_id']}", headers=HEADERS
    )
    assert queried.status_code == 200
    definition = queried.json()["data"]["clusters"][0]["generated_definition"]
    assert definition["generation_mode"] == "rule_based_candidate"
    assert set(definition["field_evidence"]) == {
        "position_name",
        "position_summary",
        "core_responsibilities",
        "required_skills",
        "bonus_skills",
        "industry_scenarios",
        "distinguishing_features",
        "representative_enterprises",
        "growth_trajectory",
    }
    assert definition["field_evidence"]["position_name"]["evidence_ids"]
