from __future__ import annotations

from copy import deepcopy
from datetime import date

from src.capability_evidence_profile import build_capability_evidence_profiles
from src.models import (
    CVExtractionResult,
    CVNormalizedResult,
    DateRange,
    Evidence,
    NormalizedSkill,
    ProjectEntry,
    ProjectFieldEvidence,
    SkillItem,
    SourcedText,
    WorkEntry,
    WorkFieldEvidence,
)


def ev(quote: str, source_id: str = "src_0001") -> Evidence:
    return Evidence(source_id=source_id, quote=quote)


def work_field(name: str, quote: str) -> WorkFieldEvidence:
    return WorkFieldEvidence(field_name=name, evidence=ev(quote))


def project_field(name: str, quote: str) -> ProjectFieldEvidence:
    return ProjectFieldEvidence(field_name=name, evidence=ev(quote))


def skill(
    item_id: str,
    name: str,
    *,
    item_type: str = "framework",
    quote: str | None = None,
    source_id: str = "src_0001",
) -> SkillItem:
    return SkillItem(
        item_id=item_id,
        name=name,
        item_type=item_type,
        evidence=ev(quote or name, source_id),
    )


def norm_skill(item_id: str, scope: str, skill_id: str, name: str) -> NormalizedSkill:
    return NormalizedSkill(
        source_item_id=item_id,
        source_scope=scope,
        source_name=name,
        skill_id=skill_id,
        canonical_name=name,
        category_code="framework",
        subcategory_code=None,
        resolution_status="resolved",
        normalization_confidence=1.0,
        resolution_source="canonical_name",
    )


def snapshot(
    *,
    skills: list[SkillItem] | None = None,
    work: list[WorkEntry] | None = None,
    projects: list[ProjectEntry] | None = None,
    normalized_skills: list[NormalizedSkill] | None = None,
) -> tuple[CVExtractionResult, CVNormalizedResult]:
    extraction = CVExtractionResult(
        document_id="cv_001",
        skills=skills or [],
        work_experience=work or [],
        project_experience=projects or [],
    )
    norm = CVNormalizedResult(
        document_id="cv_001",
        normalized_skills=normalized_skills or [],
        unresolved_items=[],
    )
    return extraction, norm


def profile_for(
    extraction: CVExtractionResult,
    normalized: CVNormalizedResult,
    *,
    as_of_date: date = date(2026, 7, 1),
):
    result = build_capability_evidence_profiles(
        extraction,
        normalized,
        as_of_date=as_of_date,
    )
    assert len(result.profiles) == 1
    return result.profiles[0]


def profiles_by_name(result) -> dict[str, object]:
    return {profile.skill_name: profile for profile in result.profiles}


def test_declared_only_evidence_level():
    extraction, normalized = snapshot(
        skills=[skill("s1", "PyTorch", quote="技能栏：PyTorch")],
        normalized_skills=[norm_skill("s1", "skills", "FRAMEWORK_PYTORCH", "PyTorch")],
    )
    profile = profile_for(extraction, normalized)

    assert profile.evidence_count == 1
    assert profile.strongest_evidence.evidence_level == "declared_only"
    assert profile.strongest_evidence.ownership == "unknown"
    assert profile.strongest_evidence.depth == "declared"


def test_project_used_evidence_level():
    project = ProjectEntry(
        entry_id="proj_001",
        name="ViT 图像分类",
        role="算法工程师",
        tech_stack=[skill("p1", "PyTorch")],
        highlights=[
            SourcedText(
                value="在 NLP 项目中使用 PyTorch 完成 ViT Fine-tuning",
                evidence=ev("在 NLP 项目中使用 PyTorch 完成 ViT Fine-tuning", "src_0002"),
            )
        ],
        evidence=ev("ViT 图像分类", "src_0002"),
        field_evidence=[
            project_field("name", "ViT 图像分类"),
            project_field("role", "算法工程师"),
        ],
    )
    extraction, normalized = snapshot(
        projects=[project],
        normalized_skills=[norm_skill("p1", "project_experience:proj_001:tech_stack", "FRAMEWORK_PYTORCH", "PyTorch")],
    )
    profile = profile_for(extraction, normalized)
    item = profile.strongest_evidence

    assert item.evidence_level == "project_used"
    assert item.ownership == "implemented"
    assert item.depth == "implemented"
    assert item.source_project_id == "proj_001"
    assert "ViT 图像分类" in item.context


def test_work_used_evidence_level():
    work = WorkEntry(
        entry_id="work_001",
        company="某科技公司",
        position="算法工程师",
        tech_stack=[skill("w1", "PyTorch")],
        responsibilities=[
            SourcedText(
                value="使用 PyTorch 开发模型训练服务",
                evidence=ev("使用 PyTorch 开发模型训练服务", "src_0002"),
            )
        ],
        evidence=ev("某科技公司", "src_0002"),
        field_evidence=[
            work_field("company", "某科技公司"),
            work_field("position", "算法工程师"),
        ],
    )
    extraction, normalized = snapshot(
        work=[work],
        normalized_skills=[norm_skill("w1", "work_experience:work_001:tech_stack", "FRAMEWORK_PYTORCH", "PyTorch")],
    )
    profile = profile_for(extraction, normalized)

    assert profile.strongest_evidence.evidence_level == "work_used"
    assert profile.strongest_evidence.ownership == "implemented"
    assert profile.strongest_evidence.source_experience_id == "work_001"


def test_owned_component_evidence_level():
    project = ProjectEntry(
        entry_id="proj_002",
        name="推荐系统",
        tech_stack=[skill("p2", "PyTorch")],
        highlights=[
            SourcedText(
                value="独立负责 PyTorch 召回模块开发",
                evidence=ev("独立负责 PyTorch 召回模块开发", "src_0002"),
            )
        ],
        evidence=ev("推荐系统", "src_0002"),
        field_evidence=[project_field("name", "推荐系统")],
    )
    extraction, normalized = snapshot(
        projects=[project],
        normalized_skills=[norm_skill("p2", "project_experience:proj_002:tech_stack", "FRAMEWORK_PYTORCH", "PyTorch")],
    )
    profile = profile_for(extraction, normalized)

    assert profile.strongest_evidence.evidence_level == "owned_component"
    assert profile.strongest_evidence.ownership == "owned"


def test_designed_system_evidence_level():
    work = WorkEntry(
        entry_id="work_002",
        company="某公司",
        position="算法工程师",
        tech_stack=[skill("w2", "PyTorch")],
        achievements=[
            SourcedText(
                value="设计 PyTorch 模型训练流水线",
                evidence=ev("设计 PyTorch 模型训练流水线", "src_0002"),
            )
        ],
        evidence=ev("某公司", "src_0002"),
        field_evidence=[
            work_field("company", "某公司"),
            work_field("position", "算法工程师"),
        ],
    )
    extraction, normalized = snapshot(
        work=[work],
        normalized_skills=[norm_skill("w2", "work_experience:work_002:tech_stack", "FRAMEWORK_PYTORCH", "PyTorch")],
    )
    profile = profile_for(extraction, normalized)

    assert profile.strongest_evidence.evidence_level == "designed_system"
    assert profile.strongest_evidence.ownership == "designed"
    assert profile.strongest_evidence.depth == "designed"


def test_measured_result_evidence_level():
    project = ProjectEntry(
        entry_id="proj_003",
        name="ViT Fine-tuning",
        tech_stack=[skill("p3", "PyTorch")],
        highlights=[
            SourcedText(
                value="使用 PyTorch 完成 ViT Fine-tuning，准确率提升至 95%",
                evidence=ev("使用 PyTorch 完成 ViT Fine-tuning，准确率提升至 95%", "src_0002"),
            )
        ],
        evidence=ev("ViT Fine-tuning", "src_0002"),
        field_evidence=[project_field("name", "ViT Fine-tuning")],
    )
    extraction, normalized = snapshot(
        projects=[project],
        normalized_skills=[norm_skill("p3", "project_experience:proj_003:tech_stack", "FRAMEWORK_PYTORCH", "PyTorch")],
    )
    profile = profile_for(extraction, normalized)

    assert profile.strongest_evidence.evidence_level == "measured_result"


def test_multi_evidence_aggregation_keeps_strongest_and_all_items():
    project = ProjectEntry(
        entry_id="proj_004",
        name="多模态项目",
        tech_stack=[skill("p4", "PyTorch")],
        highlights=[
            SourcedText(
                value="使用 PyTorch 完成 ViT Fine-tuning，准确率提升至 95%",
                evidence=ev("使用 PyTorch 完成 ViT Fine-tuning，准确率提升至 95%", "src_0002"),
            )
        ],
        evidence=ev("多模态项目", "src_0002"),
        field_evidence=[project_field("name", "多模态项目")],
    )
    work = WorkEntry(
        entry_id="work_003",
        company="某公司",
        position="算法工程师",
        tech_stack=[skill("w3", "PyTorch")],
        responsibilities=[
            SourcedText(
                value="使用 PyTorch 开发训练服务",
                evidence=ev("使用 PyTorch 开发训练服务", "src_0003"),
            )
        ],
        evidence=ev("某公司", "src_0003"),
        field_evidence=[
            work_field("company", "某公司"),
            work_field("position", "算法工程师"),
        ],
    )
    extraction, normalized = snapshot(
        skills=[skill("s2", "PyTorch", quote="技能栏：PyTorch")],
        work=[work],
        projects=[project],
        normalized_skills=[
                norm_skill("s2", "skills", "FRAMEWORK_PYTORCH", "PyTorch"),
                norm_skill("p4", "project_experience:proj_004:tech_stack", "FRAMEWORK_PYTORCH", "PyTorch"),
                norm_skill("w3", "work_experience:work_003:tech_stack", "FRAMEWORK_PYTORCH", "PyTorch"),
        ],
    )
    profile = profile_for(extraction, normalized)

    assert profile.evidence_count == 3
    assert profile.strongest_evidence.evidence_level == "measured_result"
    assert {item.evidence_level for item in profile.evidence_items} == {
        "declared_only",
        "work_used",
        "measured_result",
    }


def test_strongest_evidence_and_builder_are_deterministic():
    project = ProjectEntry(
        entry_id="proj_005",
        name="项目",
        tech_stack=[skill("p5", "PyTorch")],
        highlights=[
            SourcedText(
                value="使用 PyTorch 完成训练，准确率提升至 95%",
                evidence=ev("使用 PyTorch 完成训练，准确率提升至 95%", "src_0002"),
            )
        ],
        evidence=ev("项目", "src_0002"),
        field_evidence=[project_field("name", "项目")],
    )
    extraction, normalized = snapshot(
        projects=[project],
        normalized_skills=[norm_skill("p5", "project_experience:proj_005:tech_stack", "FRAMEWORK_PYTORCH", "PyTorch")],
    )
    first = build_capability_evidence_profiles(extraction, normalized, as_of_date=date(2026, 7, 1))
    second = build_capability_evidence_profiles(extraction, normalized, as_of_date=date(2026, 7, 1))

    assert first.model_dump() == second.model_dump()


def test_ownership_from_lead_cue():
    project = ProjectEntry(
        entry_id="proj_006",
        name="大模型训练",
        tech_stack=[skill("p6", "PyTorch")],
        highlights=[
            SourcedText(
                value="主导 PyTorch 模型训练流水线",
                evidence=ev("主导 PyTorch 模型训练流水线", "src_0002"),
            )
        ],
        evidence=ev("大模型训练", "src_0002"),
        field_evidence=[project_field("name", "大模型训练")],
    )
    extraction, normalized = snapshot(
        projects=[project],
        normalized_skills=[norm_skill("p6", "project_experience:proj_006:tech_stack", "FRAMEWORK_PYTORCH", "PyTorch")],
    )
    profile = profile_for(extraction, normalized)

    assert profile.strongest_evidence.ownership == "led"
    assert profile.strongest_evidence.depth == "led"


def test_recency_is_derived_from_experience_dates():
    entries = []
    for index, end in ((1, "2026-06"), (2, "2024-06"), (3, "2022-01")):
        entries.append(
            ProjectEntry(
                entry_id=f"proj_rec_{index}",
                name=f"项目{index}",
                date=DateRange(start="2020-01", end=end),
                tech_stack=[skill(f"p_rec_{index}", "PyTorch")],
                evidence=ev(f"项目{index}"),
                field_evidence=[
                    project_field("name", f"项目{index}"),
                    project_field("date", end),
                ],
            )
        )
    normalized_skills = [
            norm_skill(
            f"p_rec_{index}",
            f"project_experience:proj_rec_{index}:tech_stack",
            "FRAMEWORK_PYTORCH",
            "PyTorch",
        )
        for index in range(1, 4)
    ]
    result = build_capability_evidence_profiles(
        CVExtractionResult(document_id="cv_001", project_experience=entries),
        CVNormalizedResult(document_id="cv_001", normalized_skills=normalized_skills),
        as_of_date=date(2026, 7, 1),
    )
    assert len(result.profiles) == 1
    assert {item.recency for item in result.profiles[0].evidence_items} == {
        "recent",
        "moderate",
        "old",
    }


def test_context_and_evidence_lineage_are_preserved():
    project = ProjectEntry(
        entry_id="proj_007",
        name="计算机视觉",
        role="负责人",
        tech_stack=[skill("p7", "PyTorch")],
        highlights=[
            SourcedText(
                value="负责 PyTorch 模型训练与评估",
                evidence=ev("负责 PyTorch 模型训练与评估", "src_0002"),
            )
        ],
        evidence=ev("计算机视觉", "src_0002"),
        field_evidence=[
            project_field("name", "计算机视觉"),
            project_field("role", "负责人"),
        ],
    )
    extraction, normalized = snapshot(
        projects=[project],
        normalized_skills=[norm_skill("p7", "project_experience:proj_007:tech_stack", "FRAMEWORK_PYTORCH", "PyTorch")],
    )
    profile = profile_for(extraction, normalized)
    item = profile.strongest_evidence

    assert "计算机视觉" in item.context
    assert "负责人" in item.context
    assert item.source_evidence.quote == "PyTorch"
    assert any(evidence.quote == "负责 PyTorch 模型训练与评估" for evidence in item.evidence_lineage)


def test_builder_does_not_mutate_original_snapshot():
    extraction, normalized = snapshot(
        skills=[skill("s3", "PyTorch", quote="技能栏：PyTorch")],
        normalized_skills=[norm_skill("s3", "skills", "FRAMEWORK_PYTORCH", "PyTorch")],
    )
    before = deepcopy(extraction.model_dump(mode="json"))
    build_capability_evidence_profiles(extraction, normalized, as_of_date=date(2026, 7, 1))

    assert extraction.model_dump(mode="json") == before


def test_work_skill_evidence_does_not_leak_to_unrelated_stack_skills():
    work = WorkEntry(
        entry_id="work_neg_001",
        company="某科技公司",
        position="算法工程师",
        tech_stack=[
            skill("w_py", "Python", item_type="programming_language"),
            skill("w_redis", "Redis", item_type="database"),
            skill("w_docker", "Docker", item_type="tool"),
        ],
        responsibilities=[
            SourcedText(
                value="主导 Python 模型服务重构，F1 提升 12%",
                evidence=ev("主导 Python 模型服务重构，F1 提升 12%", "src_0002"),
            )
        ],
        evidence=ev("某科技公司", "src_0002"),
        field_evidence=[
            work_field("company", "某科技公司"),
            work_field("position", "算法工程师"),
        ],
    )
    normalized_skills = [
        norm_skill(
            "w_py",
            "work_experience:work_neg_001:tech_stack",
            "LANG_PYTHON",
            "Python",
        ),
        norm_skill(
            "w_redis",
            "work_experience:work_neg_001:tech_stack",
            "DATABASE_REDIS",
            "Redis",
        ),
        norm_skill(
            "w_docker",
            "work_experience:work_neg_001:tech_stack",
            "TOOL_DOCKER",
            "Docker",
        ),
    ]
    result = build_capability_evidence_profiles(
        CVExtractionResult(document_id="cv_001", work_experience=[work]),
        CVNormalizedResult(document_id="cv_001", normalized_skills=normalized_skills),
        as_of_date=date(2026, 7, 1),
    )
    by_name = profiles_by_name(result)

    python_item = by_name["Python"].strongest_evidence
    redis_item = by_name["Redis"].strongest_evidence
    docker_item = by_name["Docker"].strongest_evidence
    task_text = "主导 Python 模型服务重构，F1 提升 12%"
    assert python_item.evidence_level == "measured_result"
    assert python_item.ownership == "led"
    for weak in (redis_item, docker_item):
        assert weak.evidence_level == "work_used"
        assert weak.ownership == "unknown"
        assert weak.depth == "used"
        assert task_text not in weak.source_text
        assert all(
            evidence.quote != task_text
            for evidence in weak.evidence_lineage
        )


def test_project_skill_evidence_does_not_leak_to_other_stack_skills():
    project = ProjectEntry(
        entry_id="proj_neg_001",
        name="模型微调",
        role="算法工程师",
        tech_stack=[
            skill("p_pytorch", "PyTorch"),
            skill("p_mysql", "MySQL", item_type="database"),
            skill("p_k8s", "Kubernetes", item_type="platform"),
        ],
        highlights=[
            SourcedText(
                value="使用 PyTorch 完成模型微调，准确率提升至 95%",
                evidence=ev("使用 PyTorch 完成模型微调，准确率提升至 95%", "src_0002"),
            )
        ],
        evidence=ev("模型微调", "src_0002"),
        field_evidence=[
            project_field("name", "模型微调"),
            project_field("role", "算法工程师"),
        ],
    )
    normalized_skills = [
        norm_skill(
            "p_pytorch",
            "project_experience:proj_neg_001:tech_stack",
            "FRAMEWORK_PYTORCH",
            "PyTorch",
        ),
        norm_skill(
            "p_mysql",
            "project_experience:proj_neg_001:tech_stack",
            "DATABASE_MYSQL",
            "MySQL",
        ),
        norm_skill(
            "p_k8s",
            "project_experience:proj_neg_001:tech_stack",
            "PLATFORM_KUBERNETES",
            "Kubernetes",
        ),
    ]
    result = build_capability_evidence_profiles(
        CVExtractionResult(document_id="cv_001", project_experience=[project]),
        CVNormalizedResult(document_id="cv_001", normalized_skills=normalized_skills),
        as_of_date=date(2026, 7, 1),
    )
    by_name = profiles_by_name(result)

    pytorch_item = by_name["PyTorch"].strongest_evidence
    mysql_item = by_name["MySQL"].strongest_evidence
    k8s_item = by_name["Kubernetes"].strongest_evidence
    assert pytorch_item.evidence_level == "measured_result"
    for weak in (mysql_item, k8s_item):
        assert weak.evidence_level == "project_used"
        assert weak.evidence_level != "measured_result"
        assert weak.ownership == "unknown"
        assert weak.ownership not in {"implemented", "owned", "designed", "led"}
        assert weak.depth == "used"


def test_multi_skill_mention_in_same_task_links_both_skills():
    work = WorkEntry(
        entry_id="work_multi_001",
        company="某科技公司",
        position="后端工程师",
        tech_stack=[
            skill("w_py", "Python", item_type="programming_language"),
            skill("w_redis", "Redis", item_type="database"),
        ],
        responsibilities=[
            SourcedText(
                value="使用 Python 与 Redis 实现缓存服务",
                evidence=ev("使用 Python 与 Redis 实现缓存服务", "src_0002"),
            )
        ],
        evidence=ev("某科技公司", "src_0002"),
        field_evidence=[
            work_field("company", "某科技公司"),
            work_field("position", "后端工程师"),
        ],
    )
    normalized_skills = [
        norm_skill(
            "w_py",
            "work_experience:work_multi_001:tech_stack",
            "LANG_PYTHON",
            "Python",
        ),
        norm_skill(
            "w_redis",
            "work_experience:work_multi_001:tech_stack",
            "DATABASE_REDIS",
            "Redis",
        ),
    ]
    result = build_capability_evidence_profiles(
        CVExtractionResult(document_id="cv_001", work_experience=[work]),
        CVNormalizedResult(document_id="cv_001", normalized_skills=normalized_skills),
        as_of_date=date(2026, 7, 1),
    )
    by_name = profiles_by_name(result)

    task_text = "使用 Python 与 Redis 实现缓存服务"
    for skill_name in ("Python", "Redis"):
        item = by_name[skill_name].strongest_evidence
        assert item.source_text == task_text
        assert any(
            evidence.quote == task_text
            for evidence in item.evidence_lineage
        )
        assert item.ownership == "implemented"
        assert item.depth == "implemented"


def test_alias_mention_links_task_to_skill():
    project = ProjectEntry(
        entry_id="proj_alias_001",
        name="问答系统",
        tech_stack=[
            skill(
                "p_llm",
                "大模型",
                item_type="domain_knowledge",
                quote="大模型",
            )
        ],
        highlights=[
            SourcedText(
                value="使用 LLM 完成问答，准确率提升至 90%",
                evidence=ev("使用 LLM 完成问答，准确率提升至 90%", "src_0002"),
            )
        ],
        evidence=ev("问答系统", "src_0002"),
        field_evidence=[
            project_field("name", "问答系统"),
        ],
    )
    normalized_skills = [
        norm_skill(
            "p_llm",
            "project_experience:proj_alias_001:tech_stack",
            "AI_LLM",
            "大语言模型",
        )
    ]
    result = build_capability_evidence_profiles(
        CVExtractionResult(document_id="cv_001", project_experience=[project]),
        CVNormalizedResult(document_id="cv_001", normalized_skills=normalized_skills),
        as_of_date=date(2026, 7, 1),
        aliases_by_skill={"AI_LLM": ["LLM"]},
    )
    by_name = profiles_by_name(result)

    item = by_name["大语言模型"].strongest_evidence
    assert item.evidence_level == "measured_result"
    assert item.ownership == "implemented"
    assert "使用 LLM 完成问答，准确率提升至 90%" in item.source_text


def test_explicit_skill_evidence_span_links_task_without_name_mention():
    work = WorkEntry(
        entry_id="work_ev_001",
        company="某公司",
        tech_stack=[
            skill(
                "w_py",
                "Python",
                item_type="programming_language",
                quote="主导 Python 模型服务重构，F1 提升 12%",
            )
        ],
        responsibilities=[
            SourcedText(
                value="主导 Python 模型服务重构，F1 提升 12%",
                evidence=ev("主导 Python 模型服务重构，F1 提升 12%", "src_0002"),
            )
        ],
        evidence=ev("某公司", "src_0002"),
        field_evidence=[work_field("company", "某公司")],
    )
    normalized_skills = [
        norm_skill(
            "w_py",
            "work_experience:work_ev_001:tech_stack",
            "LANG_PYTHON",
            "Python",
        )
    ]
    result = build_capability_evidence_profiles(
        CVExtractionResult(document_id="cv_001", work_experience=[work]),
        CVNormalizedResult(document_id="cv_001", normalized_skills=normalized_skills),
        as_of_date=date(2026, 7, 1),
    )
    by_name = profiles_by_name(result)

    item = by_name["Python"].strongest_evidence
    assert item.evidence_level == "measured_result"
    assert item.ownership == "led"


def test_structured_skill_task_relation_links_task_without_name_mention():
    work = WorkEntry(
        entry_id="work_rel_001",
        company="某公司",
        tech_stack=[
            skill("w_py", "Python", item_type="programming_language")
        ],
        responsibilities=[
            SourcedText(
                value="主导模型服务重构，F1 提升 12%",
                evidence=ev("主导模型服务重构，F1 提升 12%", "src_0002"),
            )
        ],
        evidence=ev("某公司", "src_0002"),
        field_evidence=[work_field("company", "某公司")],
    )
    normalized_skills = [
        norm_skill(
            "w_py",
            "work_experience:work_rel_001:tech_stack",
            "LANG_PYTHON",
            "Python",
        )
    ]
    result = build_capability_evidence_profiles(
        CVExtractionResult(document_id="cv_001", work_experience=[work]),
        CVNormalizedResult(document_id="cv_001", normalized_skills=normalized_skills),
        as_of_date=date(2026, 7, 1),
        skill_task_relations={"w_py": [0]},
    )
    by_name = profiles_by_name(result)

    item = by_name["Python"].strongest_evidence
    assert item.evidence_level == "measured_result"
    assert item.ownership == "led"


def test_no_task_text_keeps_weak_stack_evidence():
    work = WorkEntry(
        entry_id="work_empty_001",
        company="某公司",
        tech_stack=[
            skill("w_py", "Python", item_type="programming_language"),
            skill("w_redis", "Redis", item_type="database"),
        ],
        evidence=ev("某公司", "src_0002"),
        field_evidence=[work_field("company", "某公司")],
    )
    normalized_skills = [
        norm_skill(
            "w_py",
            "work_experience:work_empty_001:tech_stack",
            "LANG_PYTHON",
            "Python",
        ),
        norm_skill(
            "w_redis",
            "work_experience:work_empty_001:tech_stack",
            "DATABASE_REDIS",
            "Redis",
        ),
    ]
    result = build_capability_evidence_profiles(
        CVExtractionResult(document_id="cv_001", work_experience=[work]),
        CVNormalizedResult(document_id="cv_001", normalized_skills=normalized_skills),
        as_of_date=date(2026, 7, 1),
    )
    by_name = profiles_by_name(result)

    for skill_name in ("Python", "Redis"):
        item = by_name[skill_name].strongest_evidence
        assert item.evidence_level == "work_used"
        assert item.ownership == "unknown"
        assert item.depth == "used"
        assert item.source_text == skill_name
        assert len(item.evidence_lineage) == 1
        assert item.context == ["某公司"]


def test_item_evidence_quote_alone_does_not_drive_measured_result():
    work = WorkEntry(
        entry_id="work_quote_001",
        company="某公司",
        tech_stack=[
            skill(
                "w_py",
                "Python",
                item_type="programming_language",
                quote="准确率提升至 99%",
            )
        ],
        evidence=ev("某公司", "src_0002"),
        field_evidence=[work_field("company", "某公司")],
    )
    normalized_skills = [
        norm_skill(
            "w_py",
            "work_experience:work_quote_001:tech_stack",
            "LANG_PYTHON",
            "Python",
        )
    ]
    result = build_capability_evidence_profiles(
        CVExtractionResult(document_id="cv_001", work_experience=[work]),
        CVNormalizedResult(document_id="cv_001", normalized_skills=normalized_skills),
        as_of_date=date(2026, 7, 1),
    )
    by_name = profiles_by_name(result)

    item = by_name["Python"].strongest_evidence
    assert item.evidence_level == "work_used"
    assert item.evidence_level != "measured_result"
    assert item.ownership == "unknown"


def test_skill_token_boundary_avoids_go_matching_google_and_c_in_cpp():
    work = WorkEntry(
        entry_id="work_token_001",
        company="某公司",
        tech_stack=[
            skill("w_go", "Go", item_type="programming_language"),
            skill("w_cpp", "C++", item_type="programming_language"),
        ],
        responsibilities=[
            SourcedText(
                value="使用 Go 编写 RPC 服务",
                evidence=ev("使用 Go 编写 RPC 服务", "src_0002"),
            ),
            SourcedText(
                value="开发 Google 搜索后端",
                evidence=ev("开发 Google 搜索后端", "src_0003"),
            ),
            SourcedText(
                value="优化 C++ 模块，延迟降低 30%",
                evidence=ev("优化 C++ 模块，延迟降低 30%", "src_0004"),
            ),
        ],
        evidence=ev("某公司", "src_0002"),
        field_evidence=[work_field("company", "某公司")],
    )
    normalized_skills = [
        norm_skill(
            "w_go",
            "work_experience:work_token_001:tech_stack",
            "LANG_GO",
            "Go",
        ),
        norm_skill(
            "w_cpp",
            "work_experience:work_token_001:tech_stack",
            "LANG_CPP",
            "C++",
        ),
    ]
    result = build_capability_evidence_profiles(
        CVExtractionResult(document_id="cv_001", work_experience=[work]),
        CVNormalizedResult(document_id="cv_001", normalized_skills=normalized_skills),
        as_of_date=date(2026, 7, 1),
    )
    by_name = profiles_by_name(result)

    go_item = by_name["Go"].strongest_evidence
    cpp_item = by_name["C++"].strongest_evidence
    assert "开发 Google 搜索后端" not in go_item.source_text
    assert any(
        evidence.quote == "使用 Go 编写 RPC 服务"
        for evidence in go_item.evidence_lineage
    )
    assert "优化 C++ 模块" in cpp_item.source_text
    assert any(
        evidence.quote == "优化 C++ 模块，延迟降低 30%"
        for evidence in cpp_item.evidence_lineage
    )


def test_ownership_fallback_is_participated_not_implemented():
    project = ProjectEntry(
        entry_id="proj_no_cue_001",
        name="数据处理",
        tech_stack=[skill("p_no_cue", "Python", item_type="programming_language")],
        highlights=[
            SourcedText(
                value="Python 数据处理任务",
                evidence=ev("Python 数据处理任务", "src_0002"),
            )
        ],
        evidence=ev("数据处理", "src_0002"),
        field_evidence=[project_field("name", "数据处理")],
    )
    normalized_skills = [
        norm_skill(
            "p_no_cue",
            "project_experience:proj_no_cue_001:tech_stack",
            "LANG_PYTHON",
            "Python",
        )
    ]
    result = build_capability_evidence_profiles(
        CVExtractionResult(document_id="cv_001", project_experience=[project]),
        CVNormalizedResult(document_id="cv_001", normalized_skills=normalized_skills),
        as_of_date=date(2026, 7, 1),
    )
    by_name = profiles_by_name(result)

    item = by_name["Python"].strongest_evidence
    assert item.ownership == "participated"
    assert item.ownership != "implemented"
    assert item.depth == "used"


def test_multi_skill_association_builder_is_deterministic():
    project = ProjectEntry(
        entry_id="proj_det_001",
        name="缓存系统",
        tech_stack=[
            skill("p_py", "Python", item_type="programming_language"),
            skill("p_redis", "Redis", item_type="database"),
        ],
        highlights=[
            SourcedText(
                value="使用 Python 与 Redis 实现缓存服务",
                evidence=ev("使用 Python 与 Redis 实现缓存服务", "src_0002"),
            )
        ],
        evidence=ev("缓存系统", "src_0002"),
        field_evidence=[project_field("name", "缓存系统")],
    )
    normalized_skills = [
        norm_skill(
            "p_py",
            "project_experience:proj_det_001:tech_stack",
            "LANG_PYTHON",
            "Python",
        ),
        norm_skill(
            "p_redis",
            "project_experience:proj_det_001:tech_stack",
            "DATABASE_REDIS",
            "Redis",
        ),
    ]

    def build():
        return build_capability_evidence_profiles(
            CVExtractionResult(document_id="cv_001", project_experience=[project]),
            CVNormalizedResult(document_id="cv_001", normalized_skills=normalized_skills),
            as_of_date=date(2026, 7, 1),
        )

    assert build().model_dump() == build().model_dump()
