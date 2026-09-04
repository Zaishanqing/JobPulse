from __future__ import annotations

from datetime import datetime, timezone

import pytest
from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1
from pydantic import ValidationError

from src.application.extraction_service import JDExtractionApplicationService
from src.models import (
    EducationRequirement,
    Evidence,
    ExperienceRequirement,
    JDExtractionResult,
    OtherRequirement,
    RequirementGraph,
    RequirementGroupChild,
    RequirementGroup,
    SkillItem,
    SkillRequirement,
)
from src.requirement_graph import (
    _Leaf,
    _leaf_matches,
    build_requirement_graph,
    validate_requirement_graph,
)
from src.requirement_graph_v0 import (
    _Leaf as V0Leaf,
    _leaf_matches as v0_leaf_matches,
    build_requirement_graph as build_requirement_graph_v0,
)

from application_fakes import FakeClient, FakePositionClassifier


def test_or_split_keeps_education_and_experience_covered() -> None:
    blocks = source_blocks("1. 计算机相关专业本科及以上学历,3 年及以上后端或平台研发经验;")
    result = extraction(
        [
            EducationRequirement(
                requirement_id="req_001",
                kind="education",
                modality="required",
                minimum_degree="bachelor",
                majors=["计算机相关专业"],
                evidence=evidence("计算机相关专业本科及以上学历"),
            ),
            ExperienceRequirement(
                requirement_id="req_002",
                kind="experience",
                modality="required",
                minimum_years=3.0,
                domain="后端或平台研发",
                evidence=evidence("3 年及以上后端或平台研发经验"),
            ),
        ],
        blocks,
    )
    graph = build_requirement_graph(result, blocks)
    assert graph.status == "complete"
    refs = {
        child.ref_id
        for group in graph.groups
        for child in group.children
        if child.node_type == "requirement_ref"
    }
    assert {"req_001", "req_002"} <= refs
    assert validate_requirement_graph(graph, {"req_001", "req_002"}, blocks) == []


def test_and_subpart_assignment_keeps_all_matched_leaves() -> None:
    block_text = (
        "3. 熟悉大模型应用开发相关技术,有以下一个或多个方向的实战经验:"
        "LLM 接入与调用编排、RAG 系统搭建与优化、Skill/MCP市场、"
        "向量数据库与检索链路设计、Context/Harness Engineering 与模型效果调优等。"
    )
    blocks = source_blocks(block_text)
    requirements = [
        OtherRequirement(
            requirement_id="req_001",
            kind="other",
            modality="required",
            label="大模型应用开发技术",
            value="熟悉大模型应用开发相关技术",
            evidence=evidence("熟悉大模型应用开发相关技术"),
        )
    ]
    for index, domain in enumerate(
        [
            "LLM 接入与调用编排",
            "RAG 系统搭建与优化",
            "Skill/MCP市场",
            "向量数据库与检索链路设计",
            "Context/Harness Engineering 与模型效果调优",
        ],
        start=2,
    ):
        requirements.append(
            ExperienceRequirement(
                requirement_id=f"req_{index:03d}",
                kind="experience",
                modality="required",
                domain=domain,
                evidence=evidence(domain),
            )
        )
    result = extraction(requirements, blocks)
    graph = build_requirement_graph(result, blocks)
    assert graph.status == "complete"
    refs = {
        child.ref_id
        for group in graph.groups
        for child in group.children
        if child.node_type == "requirement_ref"
    }
    assert {requirement.requirement_id for requirement in requirements} <= refs
    assert (
        validate_requirement_graph(
            graph,
            {requirement.requirement_id for requirement in requirements},
            blocks,
        )
        == []
    )


def source_blocks(*texts: str) -> list[dict]:
    blocks = []
    position = 0
    for index, text in enumerate(texts, start=1):
        start = position
        blocks.append(
            {
                "source_id": f"src_{index:04d}",
                "text": text,
                "start": start,
                "end": start + len(text),
            }
        )
        position = start + len(text) + 1
    return blocks


def evidence(quote: str, source_id: str = "src_0001") -> Evidence:
    return Evidence(source_id=source_id, quote=quote)


def skill_requirement(
    requirement_id: str,
    names: list[str],
    *,
    modality: str = "required",
    quote: str,
    source_id: str = "src_0001",
) -> SkillRequirement:
    return SkillRequirement(
        requirement_id=requirement_id,
        kind="skill",
        modality=modality,
        items=[
            SkillItem(name=name, item_type="programming_language")
            for name in names
        ],
        evidence=evidence(quote, source_id),
    )


def extraction(
    requirements: list[object],
    blocks: list[dict],
) -> JDExtractionResult:
    return JDExtractionResult(
        document_id="jd-1",
        requirements=requirements,
        company_facts=[],
        employment_facts=[],
        responsibilities=[],
    )


def group(
    group_id: str,
    group_type: str,
    children: list[RequirementGroupChild],
    *,
    priority: str = "required",
    evidence: Evidence | None = None,
    min_count: int | None = None,
) -> RequirementGroup:
    return RequirementGroup(
        requirement_group_id=group_id,
        group_type=group_type,
        priority=priority,
        children=children,
        min_count=min_count,
        evidence=evidence or Evidence(source_id="src_0001", quote="quote"),
    )


def ref(requirement_id: str, aspect: str | None = None) -> RequirementGroupChild:
    return RequirementGroupChild(
        node_type="requirement_ref",
        ref_id=requirement_id,
        aspect=aspect,
    )


def group_ref(group_id: str) -> RequirementGroupChild:
    return RequirementGroupChild(node_type="group_ref", ref_id=group_id)


def test_simple_must_produces_must_group():
    text = "必须熟悉 Python"
    blocks = source_blocks(text)
    result = extraction(
        [skill_requirement("req-1", ["Python"], quote=text)],
        blocks,
    )
    graph = build_requirement_graph(result, blocks)

    assert graph.status == "complete"
    assert len(graph.groups) == 1
    assert graph.groups[0].group_type == "must"
    assert graph.groups[0].priority == "required"
    assert graph.groups[0].children == (ref("req-1", "Python"),)
    assert graph.groups[0].evidence.quote == text


def test_must_and_should_produce_separate_groups():
    blocks = source_blocks("必须熟悉 Python", "优先熟悉 PyTorch")
    result = extraction(
        [
            skill_requirement(
                "req-1",
                ["Python"],
                quote="必须熟悉 Python",
                source_id="src_0001",
            ),
            skill_requirement(
                "req-2",
                ["PyTorch"],
                modality="preferred",
                quote="优先熟悉 PyTorch",
                source_id="src_0002",
            ),
        ],
        blocks,
    )
    graph = build_requirement_graph(result, blocks)

    types = {item.group_type for item in graph.groups}
    assert types == {"must", "should"}


def test_one_of_splits_single_quote_into_two_child_refs():
    text = "任选其一：NLP 或推荐系统项目"
    blocks = source_blocks(text)
    result = extraction(
        [
            OtherRequirement(
                requirement_id="req-1",
                kind="other",
                modality="required",
                label="项目背景",
                evidence=evidence(text),
            )
        ],
        blocks,
    )
    graph = build_requirement_graph(result, blocks)

    assert graph.status == "complete"
    assert graph.groups[0].group_type == "one_of"
    assert len(graph.groups[0].children) == 2
    assert {child.aspect for child in graph.groups[0].children} == {
        "NLP",
        "推荐系统项目",
    }


def test_connector_character_inside_technical_term_is_not_split():
    text = "必须掌握逻辑或运算"
    blocks = source_blocks(text)
    result = extraction(
        [skill_requirement("req-1", ["逻辑或运算"], quote=text)],
        blocks,
    )

    for builder in (build_requirement_graph, build_requirement_graph_v0):
        graph = builder(result, blocks)

        assert len(graph.groups) == 1
        assert graph.groups[0].group_type == "must"
        assert graph.groups[0].children == (ref("req-1", "逻辑或运算"),)


@pytest.mark.parametrize(
    ("aspect", "part"),
    [
        ("AI", "AI平台"),
        ("C", "C++"),
        ("SQL", "MySQL"),
        ("Java", "JavaScript"),
        ("JavaScript", "Java"),
    ],
)
def test_leaf_matching_rejects_short_skill_substrings(aspect: str, part: str):
    for leaf_type, matcher in ((_Leaf, _leaf_matches), (V0Leaf, v0_leaf_matches)):
        leaf = leaf_type(
            requirement_id="req-1",
            aspect=aspect,
            modality="required",
            quote=f"掌握 {aspect}",
            source_id="src_0001",
        )
        assert matcher(leaf, part) is False


def test_leaf_matching_accepts_exact_token_and_long_phrase_in_clause():
    short = _Leaf("req-1", "AI", "required", "掌握 AI", "src_0001")
    phrase = _Leaf(
        "req-2",
        "机器学习平台",
        "required",
        "建设机器学习平台",
        "src_0001",
    )

    assert _leaf_matches(short, "具备 AI 开发经验") is True
    assert _leaf_matches(phrase, "负责建设机器学习平台和评测系统") is True


def test_nested_and_inside_or_is_preserved():
    text = "熟悉 Python 和 PyTorch，或有 2 年以上深度学习经验"
    blocks = source_blocks(text)
    result = extraction(
        [
            skill_requirement(
                "req-1",
                ["Python", "PyTorch"],
                quote=text,
            ),
            ExperienceRequirement(
                requirement_id="req-2",
                kind="experience",
                modality="required",
                minimum_years=2,
                domain="深度学习",
                evidence=evidence(text),
            ),
        ],
        blocks,
    )
    graph = build_requirement_graph(result, blocks)

    assert graph.status == "complete"
    or_group = next(item for item in graph.groups if item.group_type == "or")
    assert len(or_group.children) == 2
    and_group = next(item for item in graph.groups if item.group_type == "and")
    assert {child.aspect for child in and_group.children} == {"Python", "PyTorch"}


def test_min_count_group_uses_min_count_and_all_children():
    text = "满足以下 3 项中的至少 2 项：熟悉 Docker、熟悉 K8s、熟悉 Linux"
    blocks = source_blocks(text)
    result = extraction(
        [
            skill_requirement("req-1", ["Docker"], quote=text),
            skill_requirement("req-2", ["K8s"], quote=text),
            skill_requirement("req-3", ["Linux"], quote=text),
        ],
        blocks,
    )
    graph = build_requirement_graph(result, blocks)

    min_count_group = next(item for item in graph.groups if item.group_type == "min_count")
    assert min_count_group.min_count == 2
    assert len(min_count_group.children) == 3


def test_experience_plus_skill_becomes_and_group():
    text = "熟悉 PyTorch，并有 2 年以上深度学习经验"
    blocks = source_blocks(text)
    result = extraction(
        [
            skill_requirement("req-1", ["PyTorch"], quote=text),
            ExperienceRequirement(
                requirement_id="req-2",
                kind="experience",
                modality="required",
                minimum_years=2,
                domain="深度学习",
                evidence=evidence(text),
            ),
        ],
        blocks,
    )
    graph = build_requirement_graph(result, blocks)

    assert any(item.group_type == "and" for item in graph.groups)
    assert {child.ref_id for child in graph.groups[0].children} == {
        "req-1",
        "req-2",
    }


def test_project_context_requirement_keeps_context_evidence():
    text = "在 NLP 项目中使用 PyTorch"
    blocks = source_blocks(text)
    result = extraction(
        [
            skill_requirement("req-1", ["PyTorch"], quote=text),
            OtherRequirement(
                requirement_id="req-2",
                kind="other",
                modality="required",
                label="项目上下文",
                evidence=evidence(text),
            ),
        ],
        blocks,
    )
    graph = build_requirement_graph(result, blocks)

    assert graph.status == "complete"
    assert all(group.evidence.quote == text for group in graph.groups)
    assert {child.ref_id for group in graph.groups for child in group.children} == {
        "req-1",
        "req-2",
    }


def test_invalid_requirement_reference_is_rejected():
    graph = RequirementGraph(
        status="complete",
        groups=[
            group(
                "g1",
                "must",
                [ref("missing")],
            )
        ],
    )
    errors = validate_requirement_graph(graph, {"req-1"})

    assert any("missing" in error for error in errors)


def test_cycle_is_rejected():
    with pytest.raises(ValidationError, match="acyclic"):
        RequirementGraph(
            status="complete",
            groups=[
                group("g1", "and", [group_ref("g2"), ref("req-1")]),
                group("g2", "and", [group_ref("g1"), ref("req-2")]),
            ],
        )


def test_graph_failure_returns_unresolved_without_raising():
    text = "必须熟悉 Python"
    result = extraction(
        [skill_requirement("req-1", ["Python"], quote=text)],
        source_blocks(text),
    )
    graph = build_requirement_graph(result, [])

    assert graph.status in {"partial", "unresolved"}
    assert graph.unresolved_items


def test_builder_is_deterministic():
    text = "熟悉 Python 和 PyTorch，或有 2 年以上深度学习经验"
    blocks = source_blocks(text)
    result = extraction(
        [
            skill_requirement("req-1", ["Python", "PyTorch"], quote=text),
            ExperienceRequirement(
                requirement_id="req-2",
                kind="experience",
                modality="required",
                minimum_years=2,
                domain="深度学习",
                evidence=evidence(text),
            ),
        ],
        blocks,
    )
    first = build_requirement_graph(result, blocks)
    second = build_requirement_graph(result, blocks)

    assert first.model_dump() == second.model_dump()


def test_application_bundle_contains_requirement_graph():
    raw_text = "必须熟悉 Python"
    source = CrawlerJDEnvelopeV1(
        source_record_id="job-1",
        source_platform="boss_zhipin",
        crawl_time=datetime.now(timezone.utc),
        raw_text=raw_text,
        raw_payload={"source": "test"},
        text_canonicalization_version="v1",
        source_version="1",
    )
    client = FakeClient(
        {
            "job_title": None,
            "responsibilities": [],
            "requirements": [
                {
                    "kind": "skill",
                    "modality": "required",
                    "items": [{"name": "Python", "item_type": "programming_language"}],
                    "proficiency": "proficient",
                    "evidence": {"source_id": "src_0001", "quote": raw_text},
                }
            ],
            "company_facts": [],
            "employment_facts": [],
        }
    )
    service = JDExtractionApplicationService(
        model="fake-model",
        normalization_path="config/normalization_map.yaml",
        client=client,
        position_classifier=FakePositionClassifier(),
        extraction_provider="fake",
        extraction_run_id="test-run",
    )
    bundle = service.extract_one(source)

    assert bundle.extraction_result.requirement_graph is not None
    assert bundle.extraction_result.requirement_graph.status == "complete"
