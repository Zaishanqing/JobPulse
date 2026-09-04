from __future__ import annotations

import pytest

from src.capability_verification import build_capability_verification
from src.models import CVMatchFeatureResult, Evidence, MatchFeature


def _feature(
    feature_id: str,
    feature_type: str,
    source_scope: str,
    raw_text: str,
    *,
    aggregation_key: str | None = None,
    occurrence_kind: str | None = None,
    resolution_status: str = "resolved",
    structured_values: dict | None = None,
) -> MatchFeature:
    values = dict(structured_values or {})
    if aggregation_key is not None:
        values["aggregation_key"] = aggregation_key
    if occurrence_kind is not None:
        values["occurrence_kind"] = occurrence_kind
    return MatchFeature(
        feature_id=feature_id,
        document_id="cv_001",
        side="cv",
        feature_type=feature_type,
        source_object_id=feature_id,
        source_scope=source_scope,
        canonical_id="LANG_PYTHON" if resolution_status == "resolved" else None,
        canonical_name="Python" if resolution_status == "resolved" else None,
        raw_text=raw_text,
        vector_text=raw_text,
        structured_values=values,
        resolution_status=resolution_status,
        evidence_refs=[Evidence(source_id="src_001", quote=raw_text)],
        taxonomy_version="2.0",
        derivation_version="test",
    )


def test_declared_only_capability_is_not_penalized():
    result = build_capability_verification(
        CVMatchFeatureResult(
            document_id="cv_001",
            as_of_date="2026-07-19",
            taxonomy_version="2.0",
            derivation_version="test",
            features=[
                _feature(
                    "skill_declared",
                    "skill",
                    "skills",
                    "Python",
                    aggregation_key="canonical:LANG_PYTHON",
                    occurrence_kind="declared",
                )
            ],
        )
    )
    assert len(result.profiles) == 1
    assert result.profiles[0].verification_status == "not_observed"
    assert result.profiles[0].evidence_bonus == 0
    assert result.evidence_links == []


def test_project_occurrence_builds_traceable_positive_evidence():
    key = "canonical:LANG_PYTHON"
    features = [
        _feature("skill_declared", "skill", "skills", "Python", aggregation_key=key,
                 occurrence_kind="declared"),
        _feature("skill_project", "skill", "project_experience:proj_001:tech_stack",
                 "Python", aggregation_key=key, occurrence_kind="project"),
        _feature("experience", "experience", "project_experience:proj_001", "推荐系统",
                 structured_values={"project_role": "负责人", "duration_months": 12}),
        _feature("task", "task", "project_experience:proj_001:highlight",
                 "使用 Python 完成训练，准确率提升至 95%"),
    ]
    result = build_capability_verification(
        CVMatchFeatureResult(
            document_id="cv_001",
            as_of_date="2026-07-19",
            taxonomy_version="2.0",
            derivation_version="test",
            features=features,
        )
    )
    profile = result.profiles[0]
    link = result.evidence_links[0]
    assert profile.verification_status == "supported"
    assert profile.evidence_bonus > 0
    assert profile.demonstrated_level in {"proficient", "advanced", "expert"}
    assert link.experience_skill_feature_id == "skill_project"
    assert link.experience_feature_id == "experience"
    assert link.supporting_task_feature_ids == ["task"]
    assert "direct_task_mention" in link.support_signals
    assert "directly_linked_measurable_outcome" in link.support_signals


def test_context_only_task_does_not_claim_direct_skill_support():
    key = "canonical:LANG_PYTHON"
    result = build_capability_verification(
        CVMatchFeatureResult(
            document_id="cv_001",
            as_of_date="2026-07-19",
            taxonomy_version="2.0",
            derivation_version="test",
            features=[
                _feature("skill_declared", "skill", "skills", "Python",
                         aggregation_key=key, occurrence_kind="declared"),
                _feature("skill_project", "skill", "project_experience:proj_001:tech_stack",
                         "Python", aggregation_key=key, occurrence_kind="project"),
                _feature("experience", "experience", "project_experience:proj_001", "推荐系统"),
                _feature("task", "task", "project_experience:proj_001:highlight",
                         "整体准确率提升至 95%"),
            ],
        )
    )
    profile = result.profiles[0]
    link = result.evidence_links[0]
    assert profile.verification_status == "partially_supported"
    assert profile.demonstrated_level == "basic"
    assert link.supporting_task_feature_ids == []
    assert link.support_signals == ["direct_experience_occurrence"]


@pytest.mark.parametrize(
    ("skill_name", "task_text", "expects_direct_mention"),
    [
        ("Java", "使用 JavaScript 完成接口开发", False),
        ("Java", "编写 Java 服务接口", True),
        ("SQL", "使用 NoSQL 存储用户数据", False),
        ("SQL", "编写 SQL 查询分析数据", True),
        ("C", "使用 C++ 实现底层算法", False),
        ("C++", "使用 C++ 实现底层算法", True),
        ("Go", "使用 golang 编写服务", False),
        ("Go", "使用 Go 编写服务", True),
        ("机器学习", "负责机器学习算法落地", True),
    ],
)
def test_task_lexical_match_uses_token_boundaries(
    skill_name: str,
    task_text: str,
    expects_direct_mention: bool,
):
    key = f"canonical:{skill_name}"
    result = build_capability_verification(
        CVMatchFeatureResult(
            document_id="cv_001",
            as_of_date="2026-07-19",
            taxonomy_version="2.0",
            derivation_version="test",
            features=[
                _feature(
                    "skill_declared",
                    "skill",
                    "skills",
                    skill_name,
                    aggregation_key=key,
                    occurrence_kind="declared",
                ),
                _feature(
                    "skill_project",
                    "skill",
                    "project_experience:proj_001:tech_stack",
                    skill_name,
                    aggregation_key=key,
                    occurrence_kind="project",
                ),
                _feature(
                    "experience",
                    "experience",
                    "project_experience:proj_001",
                    "推荐系统",
                ),
                _feature(
                    "task",
                    "task",
                    "project_experience:proj_001:highlight",
                    task_text,
                ),
            ],
        )
    )
    link = result.evidence_links[0]
    assert ("direct_task_mention" in link.support_signals) is expects_direct_mention
    if not expects_direct_mention:
        assert link.demonstrated_level == "basic"


def test_independent_direct_experiences_can_reach_advanced_level():
    key = "canonical:LANG_PYTHON"
    features = [
        _feature("skill_declared", "skill", "skills", "Python",
                 aggregation_key=key, occurrence_kind="declared"),
    ]
    for index in (1, 2):
        features.extend(
            [
                _feature(f"skill_project_{index}", "skill",
                         f"project_experience:proj_00{index}:tech_stack", "Python",
                         aggregation_key=key, occurrence_kind="project"),
                _feature(f"experience_{index}", "experience",
                         f"project_experience:proj_00{index}", f"项目{index}",
                         structured_values={"project_role": "负责人", "duration_months": 6}),
                _feature(f"task_{index}", "task",
                         f"project_experience:proj_00{index}:highlight",
                         f"使用 Python 完成项目{index}，准确率提升至 95%"),
            ]
        )
    result = build_capability_verification(
        CVMatchFeatureResult(
            document_id="cv_001",
            as_of_date="2026-07-19",
            taxonomy_version="2.0",
            derivation_version="test",
            features=features,
        )
    )
    assert result.profiles[0].independent_experience_count == 2
    assert result.profiles[0].aggregate_support_score == 10
    assert result.profiles[0].demonstrated_level == "advanced"


def test_unresolved_experience_evidence_never_adds_matching_bonus():
    key = "raw:methodology:未知方法"
    features = [
        _feature("skill_project", "skill", "project_experience:proj_001:tech_stack",
                 "未知方法", aggregation_key=key, occurrence_kind="project",
                 resolution_status="unresolved"),
        _feature("experience", "experience", "project_experience:proj_001", "未知项目"),
    ]
    result = build_capability_verification(
        CVMatchFeatureResult(
            document_id="cv_001",
            as_of_date="2026-07-19",
            taxonomy_version="2.0",
            derivation_version="test",
            features=features,
        )
    )
    assert result.profiles[0].verification_status == "unresolved"
    assert result.profiles[0].evidence_bonus == 0
