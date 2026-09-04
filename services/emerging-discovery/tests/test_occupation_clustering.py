from __future__ import annotations

from datetime import date

from app.domain.discovery import JDStructuredData, JDSnapshot, SkillReference
from app.domain.occupation_clustering import cluster_by_occupation, occupation_key


def _snapshot(index: int, title: str, skills: tuple[str, ...]) -> JDSnapshot:
    return JDSnapshot(
        jd_id=f"jd-{index}",
        schema_version="v2",
        review_status="published",
        title=title,
        source_name="source",
        publish_date=date(2026, 8, index),
        structured_data=JDStructuredData(
            responsibilities=(f"{title} 的真实职责",),
            required_skills=tuple(SkillReference(raw_skill=value) for value in skills),
            bonus_skills=(),
            business_scenarios=(),
        ),
        source_fact_id=f"fact-{index}",
        source_fact_version="v1",
        window_id="w1",
        consumption_path="published",
    )


def test_occupation_key_matches_the_frozen_experiment_contract():
    assert occupation_key("AI Agent研发工程师(大数据运维治理方向)") == "aiagent研发"
    assert occupation_key("推荐算法工程师") == "推荐算法"
    assert occupation_key("系统架构专家(图形系统)") == "系统架构"


def test_generic_llm_and_python_terms_do_not_transitively_merge_occupations():
    snapshots = (
        _snapshot(1, "大模型训练框架研发工程师", ("Python", "大模型")),
        _snapshot(2, "大模型训练框架研发专家", ("Python", "大模型")),
        _snapshot(3, "智能驾驶端到端算法工程师", ("Python", "大模型")),
    )
    clusters = cluster_by_occupation(
        snapshots,
        ((1.0, 0.0), (0.9, 0.1), (0.95, 0.05)),
    )
    by_key = {cluster.merge_basis["occupation_key"]: cluster for cluster in clusters}
    assert len(clusters) == 2
    assert {item.jd_id for item in by_key["大模型训练框架研发"].members} == {"jd-1", "jd-2"}
    assert {item.jd_id for item in by_key["智能驾驶端到端算法"].members} == {"jd-3"}
    assert all(cluster.algorithm_version == "occupation-title-key-v1" for cluster in clusters)
