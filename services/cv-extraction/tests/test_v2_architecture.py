from __future__ import annotations

import json
import inspect
import shutil
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest
from pydantic import ValidationError

from src.deepseek_client import DeepSeekResult
from src.deterministic_fields import (
    canonicalize_authoritative_fields,
    populate_deterministic_fields,
)
from src.models import CVExtractionResult
from src.match_features import build_cv_match_features
from src.local_repair import RepairTarget, apply_local_repair, plan_local_repair
from src.normalizer import load_normalization_map, lookup_skill_mapping, normalize_extraction
from src.pipeline import CVExtractionPipeline
from src.preprocess import preprocess_row
from src.exceptions import SemanticValidationError
from src.prompt_builder import (
    SPEC_PATH,
    build_local_repair_prompt,
    build_model_output_schema,
    build_system_prompt,
    build_user_prompt,
    build_validation_retry_prompt,
)
from src.provenance import align_all_evidence, canonicalize_evidence_quotes
from src.report_generator import summarize_run
from src.validator import (
    collect_raw_match_field_evidence_violations,
    collect_raw_semantic_violations,
    collect_source_coverage_requirements,
    collect_source_taxonomy_requirements,
    collect_source_section_coverage_violations,
    collect_skill_evidence_support_violations,
    collect_taxonomy_skill_coverage_violations,
    validate_business_rules,
    validate_semantic_constraints,
    validate_source_section_coverage,
    validate_skill_item_type_contract,
)
from scripts.run_extract import build_parser as build_extract_parser


def test_run_extract_default_max_workers_matches_pipeline_default() -> None:
    args = build_extract_parser().parse_args(["--input", "resumes.xlsx"])

    assert args.max_workers == 20
    assert args.api_timeout_seconds == 300
    assert inspect.signature(CVExtractionPipeline.__init__).parameters["max_workers"].default == 20
    assert (
        inspect.signature(CVExtractionPipeline.__init__)
        .parameters["api_timeout_seconds"]
        .default
        == 300
    )


def test_section_schema_contains_only_selected_fields_and_dependencies() -> None:
    schema = build_model_output_schema(("publications", "patents"))

    assert set(schema["properties"]) == {"publications", "patents"}
    assert "PublicationEntry" in schema["$defs"]
    assert "PatentEntry" in schema["$defs"]
    assert "EducationEntry" not in schema["$defs"]


def test_research_shard_prompt_keeps_research_rules_without_unrelated_sections() -> None:
    prompt = build_system_prompt(
        ("publications", "patents", "research_outputs", "self_evaluation")
    )

    assert "论文进入 `publications`" in prompt
    assert "## 9. 经历—能力验证派生层" not in prompt
    assert "## skill item_type" not in prompt
    assert len(prompt) < len(build_system_prompt())


def _raw_payload() -> dict:
    evidence = {"source_id": "src_0001", "quote": "使用 Python 开发服务"}
    work_evidence = {
        "source_id": "src_0001",
        "quote": "示例公司 开发工程师 使用 Python 开发服务",
    }
    return {
        "personal_info": None,
        "education": [],
        "work_experience": [
            {
                "company": "示例公司",
                "position": "开发工程师",
                "responsibilities": [
                    {"value": "使用 Python 开发服务", "evidence": evidence}
                ],
                "achievements": [],
                "evidence": work_evidence,
                "field_evidence": [
                    {"field_name": "company", "evidence": work_evidence},
                    {"field_name": "position", "evidence": work_evidence},
                ],
            }
        ],
        "project_experience": [],
        "skills": [
            {
                "name": "Python",
                "item_type": "programming_language",
                "proficiency": "proficient",
                "evidence": evidence,
            }
        ],
        "languages": [],
        "certificates": [
            {
                "name": "Python 证书",
                "kind": "professional_certification",
                "evidence": evidence,
            }
        ],
        "awards": [
            {"name": "一等奖", "evidence": evidence}
        ],
        "self_evaluation": [],
    }


def _validated_result_from_payload(raw_payload: dict) -> CVExtractionResult:
    payload = populate_deterministic_fields(raw_payload, "cv_000001")
    result = CVExtractionResult.model_validate(payload)
    return align_all_evidence(
        result,
        [
            {
                "source_id": "src_0001",
                "text": "示例公司 开发工程师 使用 Python 开发服务",
                "start": 0,
                "end": 24,
            }
        ],
    )


def _validated_result() -> CVExtractionResult:
    return _validated_result_from_payload(_raw_payload())


def test_model_schema_requires_evidence_and_hides_python_ids():
    schema = build_model_output_schema()
    skill = schema["$defs"]["SkillItem"]
    assert "item_id" not in skill["properties"]
    assert "evidence" in skill["required"]
    assert "entry_id" not in schema["$defs"]["CertificateEntry"]["properties"]
    assert "competition_award" not in schema["$defs"]["CertificateEntry"]["properties"]["kind"]["enum"]
    personal_binding = schema["$defs"]["PersonalFieldEvidence"]
    assert "name" not in personal_binding["properties"]["field_name"]["enum"]
    assert "name" in schema["$defs"]["ProjectFieldEvidence"]["properties"]["field_name"]["enum"]
    assert SPEC_PATH.read_text(encoding="utf-8").strip() in build_system_prompt()


def test_competition_awards_and_credentials_have_disjoint_collections() -> None:
    payload = _raw_payload()
    payload["certificates"] = [
        {
            "name": "创新竞赛一等奖",
            "kind": "competition_award",
            "evidence": {"source_id": "src_0001", "quote": "创新竞赛一等奖"},
        }
    ]
    payload["awards"] = [
        {
            "name": "CET-6",
            "evidence": {"source_id": "src_0002", "quote": "CET-6 通过"},
        }
    ]
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(payload, "cv_classification")
    )

    with pytest.raises(SemanticValidationError) as exc_info:
        validate_semantic_constraints(result)

    assert exc_info.value.violations == [
        {
            "code": "credential_award_misclassified",
            "entry_id": "cert_001",
            "source_collection": "certificates",
            "expected_collection": "awards",
            "source_id": "src_0001",
        },
        {
            "code": "credential_award_misclassified",
            "entry_id": "award_001",
            "source_collection": "awards",
            "expected_collection": "certificates",
            "source_id": "src_0002",
        },
    ]


def test_missing_skill_evidence_is_rejected():
    payload = populate_deterministic_fields(_raw_payload(), "cv_000001")
    del payload["skills"][0]["evidence"]
    with pytest.raises(ValidationError, match="evidence"):
        CVExtractionResult.model_validate(payload)


def test_recursive_alignment_covers_nested_facts_certificates_and_awards():
    result = _validated_result()
    assert result.skills[0].evidence.alignment == "exact"
    assert result.work_experience[0].responsibilities[0].evidence.alignment == "exact"
    assert result.certificates[0].evidence.alignment == "exact"
    assert result.awards[0].evidence.alignment == "exact"


def test_match_relevant_scalar_without_field_evidence_is_rejected():
    payload = _raw_payload()
    payload["work_experience"][0]["field_evidence"] = [
        payload["work_experience"][0]["field_evidence"][0]
    ]
    with pytest.raises(ValidationError, match="field_evidence mismatch"):
        CVExtractionResult.model_validate(
            populate_deterministic_fields(payload, "cv_000001")
        )


def test_dangling_personal_field_evidence_is_rejected_during_schema_validation():
    payload = _raw_payload()
    evidence = {"source_id": "src_0001", "quote": "开发工程师"}
    payload["personal_info"] = {
        "evidence": evidence,
        "field_evidence": [
            {"field_name": "expected_position", "evidence": evidence}
        ],
    }
    with pytest.raises(ValidationError, match=r"unexpected=\['expected_position'\]"):
        CVExtractionResult.model_validate(
            populate_deterministic_fields(payload, "cv_000001")
        )


def test_match_field_evidence_must_lexically_support_plain_text_value():
    payload = _raw_payload()
    payload["work_experience"][0]["position"] = "数据科学家"
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(payload, "cv_000001")
    )
    with pytest.raises(SemanticValidationError, match="unsupported_fields"):
        validate_semantic_constraints(result)


def test_unknown_degree_is_valid_but_produces_unresolved_education_feature():
    payload = _raw_payload()
    evidence = {"source_id": "src_0001", "quote": "示例公司 开发工程师 使用 Python 开发服务"}
    payload["education"] = [
        {
            "school": "示例公司",
            "major": "Python",
            "degree": "unknown",
            "evidence": evidence,
            "field_evidence": [
                {"field_name": "school", "evidence": evidence},
                {"field_name": "major", "evidence": evidence},
            ],
        }
    ]
    result = align_all_evidence(
        CVExtractionResult.model_validate(
            populate_deterministic_fields(payload, "cv_000001")
        ),
        [{"source_id": "src_0001", "text": evidence["quote"], "start": 0, "end": 24}],
    )
    validate_semantic_constraints(result)
    normalization = load_normalization_map("resources/normalization/2.0/normalization_map.yaml")
    profile = build_cv_match_features(
        result,
        normalize_extraction(result, normalization),
        normalization,
        as_of_date=pd.Timestamp("2026-07-18").date(),
    )
    education = next(
        feature
        for feature in profile.features
        if feature.feature_type == "education" and feature.source_scope.startswith("education:edu_")
    )
    assert education.resolution_status == "unresolved"
    assert education.canonical_id is None


def test_work_without_explicit_position_keeps_experience_without_role():
    payload = _raw_payload()
    work = payload["work_experience"][0]
    work.pop("position")
    work["field_evidence"] = [
        binding for binding in work["field_evidence"] if binding["field_name"] != "position"
    ]
    result = _validated_result_from_payload(payload)
    validate_semantic_constraints(result)
    normalization = load_normalization_map("resources/normalization/2.0/normalization_map.yaml")
    profile = build_cv_match_features(
        result,
        normalize_extraction(result, normalization),
        normalization,
        as_of_date=pd.Timestamp("2026-07-18").date(),
    )
    assert not any(feature.feature_type == "role" for feature in profile.features)
    experience = next(
        feature for feature in profile.features if feature.source_scope.startswith("work_experience:work_")
    )
    task = next(feature for feature in profile.features if feature.feature_type == "task")
    assert experience.raw_text == "示例公司"
    assert task.vector_text == "示例公司；使用 Python 开发服务"


def test_role_features_wait_for_position_taxonomy_v2_resolution():
    result = _validated_result()
    normalization = load_normalization_map("resources/normalization/2.0/normalization_map.yaml")
    profile = build_cv_match_features(
        result,
        normalize_extraction(result, normalization),
        normalization,
        as_of_date=pd.Timestamp("2026-07-18").date(),
    )
    roles = [feature for feature in profile.features if feature.feature_type == "role"]
    assert roles
    assert all(feature.resolution_status == "unresolved" for feature in roles)
    assert all(feature.canonical_id is None for feature in roles)


def test_normalization_is_a_formal_model_and_keeps_source_item_reference():
    result = _validated_result()
    normalization = load_normalization_map("resources/normalization/2.0/normalization_map.yaml")
    normalized = normalize_extraction(result, normalization)
    assert normalized.document_id == result.document_id
    assert normalized.normalized_skills[0].source_item_id == result.skills[0].item_id
    assert normalized.normalized_skills[0].skill_id == "LANG_PYTHON"
    assert normalized.normalized_skills[0].normalization_confidence == 1.0
    assert normalized.normalized_skills[0].resolution_source == "canonical_name"


def test_normalization_marks_curated_noncanonical_name_as_alias():
    result = _validated_result()
    result.skills[0].name = "RAG框架"
    result.skills[0].item_type = "methodology"
    normalization = load_normalization_map("resources/normalization/2.0/normalization_map.yaml")

    normalized = normalize_extraction(result, normalization)

    assert normalized.normalized_skills[0].skill_id == "AI_RAG"
    assert normalized.normalized_skills[0].normalization_confidence == 1.0
    assert normalized.normalized_skills[0].resolution_source == "alias"


def test_taxonomy_contract_rejects_shared_affix_composite_skill() -> None:
    payload = _raw_payload()
    payload["skills"][0]["name"] = "单元与接口测试"
    payload["skills"][0]["item_type"] = "methodology"
    payload["skills"][0]["evidence"]["quote"] = "单元与接口测试"
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(payload, "cv_000001")
    )
    normalization = load_normalization_map("resources/normalization/2.0/normalization_map.yaml")

    with pytest.raises(SemanticValidationError) as exc_info:
        validate_skill_item_type_contract(result, normalization)

    assert exc_info.value.violations == [
        {
            "code": "composite_skill_item",
            "item_id": result.skills[0].item_id,
            "name": "单元与接口测试",
            "parts": ["单元测试", "接口测试"],
        }
    ]


def test_business_review_flags_reference_generated_skill_ids():
    result = _validated_result()
    result.skills[0].proficiency = "unknown"
    flags = validate_business_rules(result)
    skill_flags = [flag for flag in flags if flag["rule_scope"] == "skill"]
    assert skill_flags
    assert {flag["item_id"] for flag in skill_flags} == {result.skills[0].item_id}


def test_skill_review_flags_are_scoped_like_jd_requirements():
    payload = _raw_payload()
    evidence = {"source_id": "src_0001", "quote": "使用 Python 开发服务"}
    payload["skills"].append({
        "name": "Python", "item_type": "programming_language", "evidence": evidence,
    })
    payload["project_experience"] = [{
        "name": "Python 开发服务",
        "tech_stack": [{
            "name": "Python", "item_type": "programming_language", "evidence": evidence,
        }],
        "highlights": [],
        "evidence": evidence,
        "field_evidence": [{"field_name": "name", "evidence": evidence}],
    }]
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(payload, "cv_000001")
    )
    flags = validate_business_rules(result)
    duplicate_flags = [flag for flag in flags if flag["issue_type"] == "duplicate_skill"]
    proficiency_flags = [
        flag for flag in flags if flag["issue_type"] == "unknown_skill_proficiency"
    ]
    assert [flag["item_id"] for flag in duplicate_flags] == [result.skills[1].item_id]
    assert [flag["item_id"] for flag in proficiency_flags] == [result.skills[1].item_id]
    assert result.project_experience[0].tech_stack[0].item_id not in {
        flag["item_id"] for flag in flags if "item_id" in flag
    }


def test_semantic_gate_rejects_named_skill_descriptors_and_sentence_project_names():
    payload = _raw_payload()
    project_evidence = {"source_id": "src_0001", "quote": "• 搭建推荐系统。"}
    payload["skills"][0]["name"] = "Python能力"
    payload["skills"][0]["item_type"] = "programming_language"
    payload["project_experience"] = [{
        "name": "• 搭建推荐系统。",
        "tech_stack": [],
        "highlights": [],
        "evidence": project_evidence,
        "field_evidence": [{"field_name": "name", "evidence": project_evidence}],
    }]
    payload["skills"][0]["item_type"] = "tool"
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(payload, "cv_000001")
    )
    with pytest.raises(SemanticValidationError) as exc_info:
        validate_semantic_constraints(result)
    assert {item["code"] for item in exc_info.value.violations} == {
        "descriptive_skill_item", "invalid_project_name_shape"
    }


@pytest.mark.parametrize("name", ["RAG会话检索", "Skills声明式加载"])
def test_semantic_gate_preserves_standalone_capability_noun_phrases(name):
    payload = _raw_payload()
    payload["skills"][0]["name"] = name
    payload["skills"][0]["item_type"] = "methodology"
    materialized = populate_deterministic_fields(payload, "cv_000001")
    result = CVExtractionResult.model_validate(materialized)
    validate_semantic_constraints(result)


def test_experience_skill_proficiency_requires_an_explicit_cue_in_its_evidence():
    payload = _raw_payload()
    payload["work_experience"][0]["tech_stack"] = [
        {
            "name": "Python",
            "item_type": "programming_language",
            "proficiency": "proficient",
            "evidence": {
                "source_id": "src_0001",
                "quote": "使用 Python 开发服务",
            },
        }
    ]
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(payload, "cv_000001")
    )
    with pytest.raises(SemanticValidationError) as exc_info:
        validate_semantic_constraints(result)
    assert any(
        item["code"] == "unsupported_experience_skill_proficiency"
        for item in exc_info.value.violations
    )
    result.work_experience[0].tech_stack[0].evidence.quote = "熟练使用 Python"
    validate_semantic_constraints(result)


def test_language_proficiency_requires_explicit_same_evidence_level() -> None:
    payload = _raw_payload()
    payload["languages"] = [
        {
            "language": "英语",
            "proficiency": "professional",
            "evidence": {
                "source_id": "src_0001",
                "quote": "能够阅读英文论文并复现实验。",
            },
        }
    ]
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(payload, "cv_language_level")
    )

    with pytest.raises(SemanticValidationError) as exc_info:
        validate_semantic_constraints(result)

    assert exc_info.value.violations == [
        {
            "code": "unsupported_language_proficiency",
            "entry_id": "lang_001",
            "language": "英语",
            "proficiency": "professional",
            "source_id": "src_0001",
            "evidence_quote": "能够阅读英文论文并复现实验。",
        }
    ]
    result.languages[0].proficiency = "unknown"
    validate_semantic_constraints(result)


def test_source_section_coverage_rejects_an_entirely_omitted_work_segment():
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(_raw_payload(), "cv_000001")
    )
    source_blocks = [
        {"source_id": "src_0001", "text": "实习经历"},
        {"source_id": "src_0002", "text": "【某公司，名称已遮挡】"},
        {"source_id": "src_0003", "text": "2025.06-2025.12"},
        {"source_id": "src_0004", "text": "负责分布式任务调度平台架构重构与高并发控制机制开发，支持数千节点稳定运行"},
        {"source_id": "src_0005", "text": "专业技能"},
    ]
    with pytest.raises(SemanticValidationError) as exc_info:
        validate_source_section_coverage(result, source_blocks)
    assert {item["source_id"] for item in exc_info.value.violations} == {
        "src_0002", "src_0003", "src_0004"
    }
    assert all(
        item["code"] == "source_section_uncovered"
        for item in exc_info.value.violations
    )


def test_source_coverage_gate_requirements_are_exposed_before_extraction() -> None:
    blocks = [
        {"source_id": "src_0001", "text": "工作经历"},
        {
            "source_id": "src_0002",
            "text": "负责服务接口设计、并发控制和发布验证，确保关键请求可追踪。",
        },
        {"source_id": "src_0003", "text": "专业技能"},
    ]

    assert collect_source_coverage_requirements(blocks) == [
        {
            "source_id": "src_0002",
            "section": "work",
            "expected_collections": ["work_experience", "project_experience"],
        }
    ]


def test_personal_info_pure_name_evidence_requires_name_field() -> None:
    payload = populate_deterministic_fields(_raw_payload(), "cv_personal_name")
    payload["personal_info"] = {
        "evidence": {
            "source_id": "src_0001",
            "quote": "测试姓名",
            "start": None,
            "end": None,
            "alignment": "unresolved",
            "occurrence_index": None,
        },
        "field_evidence": [],
    }

    violations = collect_source_section_coverage_violations(
        payload,
        [{"source_id": "src_0001", "text": "测试姓名"}],
    )

    assert violations == [
        {
            "code": "explicit_personal_name_uncovered",
            "entry_id": "personal_info",
            "source_id": "src_0001",
            "source_text": "测试姓名",
        }
    ]


@pytest.mark.parametrize(
    "header",
    [
        "# 测试姓名 — 后端工程师",
        "测试姓名 目标岗位:后端工程师",
        "测试姓名 Python后端工程师",
    ],
)
def test_structured_personal_header_requires_name_field(header: str) -> None:
    payload = populate_deterministic_fields(_raw_payload(), "cv_header_name")
    payload["personal_info"] = {
        "expected_position": "后端工程师",
        "evidence": {
            "source_id": "src_0001",
            "quote": header,
            "start": None,
            "end": None,
            "alignment": "unresolved",
            "occurrence_index": None,
        },
        "field_evidence": [
            {
                "field_name": "expected_position",
                "evidence": {
                    "source_id": "src_0001",
                    "quote": "后端工程师",
                    "start": None,
                    "end": None,
                    "alignment": "unresolved",
                    "occurrence_index": None,
                },
            }
        ],
    }

    violations = collect_source_section_coverage_violations(
        payload,
        [{"source_id": "src_0001", "text": header}],
    )

    assert [item["code"] for item in violations] == [
        "explicit_personal_name_uncovered"
    ]


def test_decorated_non_header_text_is_not_treated_as_personal_name() -> None:
    payload = populate_deterministic_fields(_raw_payload(), "cv_no_header_name")
    payload["personal_info"] = None
    blocks = [
        {"source_id": "src_0001", "text": "个人简历 后端工程师"},
        {"source_id": "src_0002", "text": "联系方式已隐藏"},
        {"source_id": "src_0003", "text": "教育经历"},
        {"source_id": "src_0004", "text": "技术技能 — Python / Java"},
    ]

    assert collect_source_section_coverage_violations(payload, blocks) == []


def test_explicit_language_capability_requires_language_evidence() -> None:
    text = "时间表述含中文年月；可进行英文技术文档阅读。"
    payload = populate_deterministic_fields(_raw_payload(), "cv_language")
    payload["languages"] = []

    violations = collect_source_section_coverage_violations(
        payload,
        [
            {"source_id": "src_0031", "text": text},
            {"source_id": "src_0032", "text": "能够阅读英文论文并复现实验。"},
        ],
    )

    assert violations == [
        {
            "code": "explicit_language_uncovered",
            "language": "English",
            "source_id": "src_0031",
            "source_text": text,
            "suggested_append_collection": "languages",
        }
    ]


def test_explicit_language_capability_is_covered_by_same_source() -> None:
    text = "时间表述含中文年月；可进行英文技术文档阅读。"
    payload = populate_deterministic_fields(_raw_payload(), "cv_language")
    payload["languages"] = [
        {
            "entry_id": "lang_001",
            "language": "英语",
            "proficiency": "unknown",
            "evidence": {"source_id": "src_0031", "quote": "可进行英文技术文档阅读。"},
        }
    ]

    assert collect_source_section_coverage_violations(
        payload,
        [{"source_id": "src_0031", "text": text}],
    ) == []


def test_activity_title_cannot_be_used_as_project_identifier() -> None:
    payload = _raw_payload()
    evidence = {"source_id": "src_0001", "quote": "学院工程分享"}
    payload["project_experience"] = [
        {
            "name": "学院工程分享",
            "tech_stack": [],
            "highlights": [],
            "evidence": evidence,
            "field_evidence": [{"field_name": "name", "evidence": evidence}],
        }
    ]
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(payload, "cv_activity")
    )

    with pytest.raises(SemanticValidationError) as exc_info:
        validate_semantic_constraints(result)

    assert exc_info.value.violations == [
        {
            "code": "activity_title_as_project",
            "entry_id": "proj_001",
            "name": "学院工程分享",
        }
    ]


def test_internship_title_is_a_role_not_a_project_identifier() -> None:
    payload = _raw_payload()
    evidence = {"source_id": "src_0001", "quote": "前端实习"}
    payload["project_experience"] = [
        {
            "name": "前端实习",
            "tech_stack": [],
            "highlights": [],
            "evidence": evidence,
            "field_evidence": [{"field_name": "name", "evidence": evidence}],
        }
    ]
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(payload, "cv_internship")
    )

    with pytest.raises(SemanticValidationError) as exc_info:
        validate_semantic_constraints(result)

    assert exc_info.value.violations == [
        {
            "code": "role_title_as_project",
            "entry_id": "proj_001",
            "name": "前端实习",
            "source_id": "src_0001",
            "expected_collection": "work_experience",
        }
    ]


def test_research_project_heading_cannot_become_employment() -> None:
    payload = _raw_payload()
    evidence = {
        "source_id": "src_0002",
        "quote": "1. 博士课题|示例并行计算组|2022.09—至今",
    }
    payload["work_experience"] = [
        {
            "company": "示例并行计算组",
            "tech_stack": [],
            "responsibilities": [],
            "achievements": [],
            "evidence": evidence,
            "field_evidence": [
                {"field_name": "company", "evidence": evidence}
            ],
        }
    ]
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(payload, "cv_research_project")
    )

    with pytest.raises(SemanticValidationError) as exc_info:
        validate_semantic_constraints(result)

    assert exc_info.value.violations == [
        {
            "code": "research_project_as_work",
            "entry_id": "work_001",
            "source_collection": "work_experience",
            "expected_collection": "project_experience",
            "source_id": "src_0002",
        }
    ]


@pytest.mark.parametrize("role_title", ["学生会学术部部长", "项目助理"])
def test_semantic_gate_rejects_practice_role_titles_disguised_as_projects(
    role_title: str,
):
    evidence = {"source_id": "src_0002", "quote": role_title}
    payload = _raw_payload()
    payload["project_experience"] = [
        {
            "name": role_title,
            "tech_stack": [],
            "highlights": [],
            "evidence": evidence,
            "field_evidence": [{"field_name": "name", "evidence": evidence}],
        }
    ]
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(payload, "cv_000001")
    )
    validate_source_section_coverage(
        result,
        [
            {"source_id": "src_0001", "text": "实践经历"},
            {"source_id": "src_0002", "text": role_title},
            {"source_id": "src_0003", "text": "专业技能"},
        ],
    )
    with pytest.raises(SemanticValidationError) as exc_info:
        validate_semantic_constraints(result)
    assert any(
        item["code"] == "role_title_as_project"
        and item["expected_collection"] == "work_experience"
        for item in exc_info.value.violations
    )


def test_unowned_practice_date_authorizes_one_contextual_work_append():
    payload = populate_deterministic_fields(_raw_payload(), "cv_000001")
    payload["work_experience"] = []
    payload["project_experience"] = []
    blocks = [
        {"source_id": "src_0001", "text": "校园经历"},
        {"source_id": "src_0002", "text": "某大学"},
        {"source_id": "src_0003", "text": "离散数学教学助理"},
        {"source_id": "src_0004", "text": "2023.01-2023.05"},
        {"source_id": "src_0005", "text": "附加信息"},
    ]

    violations = collect_source_section_coverage_violations(payload, blocks)
    plan = plan_local_repair(
        payload,
        "SemanticValidationError",
        violations,
        blocks,
    )

    assert violations[0]["suggested_append_collection"] == "work_experience"
    assert violations[0]["context_source_ids"] == [
        "src_0001", "src_0002", "src_0003", "src_0004", "src_0005"
    ]
    assert plan is not None
    assert plan.targets == ()
    assert plan.append_collections == ("work_experience",)
    assert plan.required_append_counts == (("work_experience", 1),)


def test_raw_field_evidence_collection_exposes_latent_errors_before_schema():
    payload = populate_deterministic_fields(_raw_payload(), "cv_000001")
    payload["education"] = [
        {
            "entry_id": "edu_001",
            "school": "示例大学",
            "major": "计算机",
            "gpa": "3.8/4.0",
            "field_evidence": [
                {
                    "field_name": "school",
                    "evidence": {"source_id": "src_0001", "quote": "示例大学"},
                },
                {
                    "field_name": "major",
                    "evidence": {"source_id": "src_0001", "quote": "计算机"},
                },
            ],
        }
    ]

    violations = collect_raw_match_field_evidence_violations(payload)

    education = next(item for item in violations if item["entry_id"] == "edu_001")
    assert education["missing_fields"] == ["gpa"]


def test_raw_semantic_preflight_localizes_role_project_before_schema_passes():
    payload = populate_deterministic_fields(_raw_payload(), "cv_000001")
    evidence = {"source_id": "src_0002", "quote": "新媒体中心负责人"}
    payload["project_experience"] = [
        {
            "entry_id": "proj_001",
            "name": "新媒体中心负责人",
            "evidence": evidence,
            "field_evidence": [{"field_name": "name", "evidence": evidence}],
            "tech_stack": [],
            "highlights": [],
        }
    ]

    violations = collect_raw_semantic_violations(payload)
    role = next(item for item in violations if item["code"] == "role_title_as_project")
    plan = plan_local_repair(
        payload,
        "SemanticValidationError",
        violations,
        [{"source_id": "src_0002", "text": "新媒体中心负责人"}],
    )

    assert role["entry_id"] == "proj_001"
    assert plan is not None
    assert plan.targets[0].collection == "project_experience"
    assert plan.targets[0].index == 0
    assert plan.append_collections == ("work_experience",)
    assert plan.required_append_counts == (("work_experience", 1),)


def test_ambiguous_uncovered_project_fact_authorizes_bounded_candidate_entries():
    payload = populate_deterministic_fields(_raw_payload(), "cv_000001")
    payload["work_experience"] = []
    payload["project_experience"] = []
    for name, source_id in (("项目甲", "src_0002"), ("项目乙", "src_0006")):
        evidence = {"source_id": source_id, "quote": name}
        payload["project_experience"].append(
            {
                "name": name,
                "evidence": evidence,
                "field_evidence": [{"field_name": "name", "evidence": evidence}],
                "tech_stack": [],
                "highlights": [],
            }
        )
    payload = populate_deterministic_fields(payload, "cv_000001")
    blocks = [
        {"source_id": "src_0001", "text": "项目经历"},
        {"source_id": "src_0002", "text": "项目甲"},
        {"source_id": "src_0003", "text": "项目甲背景说明"},
        {
            "source_id": "src_0004",
            "text": "完成核心模型训练与部署优化，使在线推理吞吐量获得显著提升并稳定运行",
        },
        {"source_id": "src_0005", "text": "项目乙背景说明"},
        {"source_id": "src_0006", "text": "项目乙"},
    ]

    violations = collect_source_section_coverage_violations(payload, blocks)
    uncovered = next(item for item in violations if item["source_id"] == "src_0004")
    plan = plan_local_repair(
        payload, "SemanticValidationError", [uncovered], blocks
    )

    assert {item["entry_id"] for item in uncovered["candidate_owners"]} == {
        "proj_001", "proj_002"
    }
    assert plan is not None
    assert {(target.collection, target.index) for target in plan.targets} == {
        ("project_experience", 0), ("project_experience", 1)
    }


def test_local_repair_preserves_supported_derived_field_evidence_closure():
    payload = populate_deterministic_fields(_raw_payload(), "cv_000001")
    evidence = {"source_id": "src_0002", "quote": "示例大学（研究生）计算机科学"}
    payload["education"] = [
        {
            "entry_id": "edu_001",
            "school": "示例大学",
            "major": "计算机科学",
            "degree": "unknown",
            "evidence": evidence,
            "field_evidence": [
                {"field_name": "school", "evidence": evidence},
                {"field_name": "major", "evidence": evidence},
                {"field_name": "degree", "evidence": evidence},
            ],
        }
    ]
    plan = plan_local_repair(
        payload,
        "SemanticValidationError",
        [{"code": "invalid_match_field_evidence", "entry_id": "edu_001"}],
        [{"source_id": "src_0002", "text": evidence["quote"]}],
    )
    replacement = {
        "school": "示例大学",
        "major": "计算机科学",
        "degree": "master",
        "evidence": evidence,
        "field_evidence": [
            {"field_name": "school", "evidence": evidence},
            {"field_name": "major", "evidence": evidence},
        ],
    }
    assert plan is not None
    repaired = apply_local_repair(
        payload,
        {
            "operations": [
                {
                    "op": "replace",
                    "target": {"collection": "education", "index": 0},
                    "value": replacement,
                }
            ]
        },
        plan,
    )

    assert [
        item["field_name"] for item in repaired["education"][0]["field_evidence"]
    ] == ["school", "major", "degree"]


def test_taxonomy_skill_coverage_detects_explicit_experience_skill_omission():
    payload = _raw_payload()
    payload["work_experience"][0]["tech_stack"] = []
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(payload, "cv_000001")
    )
    normalization = load_normalization_map("resources/normalization/2.0/normalization_map.yaml")
    violations = collect_taxonomy_skill_coverage_violations(
        result,
        normalization,
        [
            {"source_id": "src_0000", "text": "工作经历"},
            {
                "source_id": "src_0001",
                "text": "示例公司 开发工程师 使用 Python 开发服务",
            },
        ],
    )

    assert any(
        item["code"] == "taxonomy_skill_uncovered"
        and item["name"] == "Python"
        and item["entry_id"] == "work_001"
        for item in violations
    )


def test_taxonomy_coverage_is_collected_before_schema_is_valid() -> None:
    payload = populate_deterministic_fields(_raw_payload(), "cv_raw_taxonomy")
    docker_fact = "通过Docker镜像保证结果可复现。"
    payload["project_experience"] = [
        {
            "entry_id": "proj_001",
            "name": "实验复现平台",
            "evidence": {"source_id": "src_0002", "quote": "实验复现平台"},
            "field_evidence": [],
            "tech_stack": [],
            "highlights": [
                {
                    "value": docker_fact,
                    "evidence": {"source_id": "src_0003", "quote": docker_fact},
                }
            ],
        }
    ]
    normalization = load_normalization_map(
        "resources/normalization/2.0/normalization_map.yaml"
    )

    violations = collect_taxonomy_skill_coverage_violations(
        payload,
        normalization,
        [
            {"source_id": "src_0001", "text": "项目经历"},
            {"source_id": "src_0002", "text": "实验复现平台"},
            {"source_id": "src_0003", "text": docker_fact},
        ],
    )

    assert any(
        item["code"] == "taxonomy_skill_uncovered"
        and item["name"] == "Docker"
        and item["entry_id"] == "proj_001"
        for item in violations
    )


def test_taxonomy_coverage_uses_evidence_owner_without_project_heading() -> None:
    payload = populate_deterministic_fields(_raw_payload(), "cv_owner_coverage")
    skill_fact = "基于 Megatron-LM 优化分布式训练吞吐。"
    context_before = "负责多模态模型训练流水线。"
    context_after = "训练吞吐提升百分之二十。"
    payload["project_experience"] = [
        {
            "entry_id": "proj_001",
            "name": "多模态模型训练",
            "evidence": {"source_id": "src_0001", "quote": "多模态模型训练"},
            "field_evidence": [],
            "tech_stack": [],
            "highlights": [
                {
                    "value": context_before,
                    "evidence": {"source_id": "src_0002", "quote": context_before},
                },
                {
                    "value": context_after,
                    "evidence": {"source_id": "src_0004", "quote": context_after},
                },
            ],
        }
    ]
    normalization = load_normalization_map(
        "resources/normalization/2.0/normalization_map.yaml"
    )
    blocks = [
        {"source_id": "src_0001", "text": "多模态模型训练"},
        {"source_id": "src_0002", "text": context_before},
        {"source_id": "src_0003", "text": skill_fact},
        {"source_id": "src_0004", "text": context_after},
    ]

    violations = collect_taxonomy_skill_coverage_violations(
        payload,
        normalization,
        blocks,
    )

    assert any(
        item["code"] == "taxonomy_skill_uncovered"
        and item["name"] == "Megatron-LM"
        and item["entry_id"] == "proj_001"
        for item in violations
    )
    assert any(
        item["code"] == "project_tech_stack_catastrophic_omission"
        and item["entry_id"] == "proj_001"
        for item in violations
    )
    plan = plan_local_repair(
        payload,
        "SemanticValidationError",
        violations,
        blocks,
    )
    assert plan is not None
    assert plan.targets == (RepairTarget("project_experience", 0),)
    assert [item["source_id"] for item in plan.source_blocks] == [
        "src_0001",
        "src_0002",
        "src_0003",
        "src_0004",
    ]


def test_taxonomy_coverage_uses_unique_exact_evidence_owner_without_heading() -> None:
    payload = populate_deterministic_fields(_raw_payload(), "cv_exact_owner_coverage")
    skill_fact = "基于 Megatron-LM 优化分布式训练吞吐。"
    payload["project_experience"] = [
        {
            "entry_id": "proj_001",
            "name": "多模态模型训练",
            "evidence": {"source_id": "src_0001", "quote": "多模态模型训练"},
            "field_evidence": [],
            "tech_stack": [],
            "highlights": [
                {
                    "value": skill_fact,
                    "evidence": {"source_id": "src_0002", "quote": skill_fact},
                }
            ],
        }
    ]
    blocks = [
        {"source_id": "src_0001", "text": "多模态模型训练"},
        {"source_id": "src_0002", "text": skill_fact},
    ]
    normalization = load_normalization_map(
        "resources/normalization/2.0/normalization_map.yaml"
    )

    violations = collect_taxonomy_skill_coverage_violations(
        payload,
        normalization,
        blocks,
    )

    assert any(
        item["code"] == "taxonomy_skill_uncovered"
        and item["name"] == "Megatron-LM"
        and item["entry_id"] == "proj_001"
        and item["source_id"] == "src_0002"
        for item in violations
    )


def test_catastrophic_project_skill_omission_repairs_every_project() -> None:
    payload = populate_deterministic_fields(_raw_payload(), "cv_all_projects_empty")
    payload["project_experience"] = [
        {
            "entry_id": "proj_001",
            "name": "模型训练",
            "evidence": {"source_id": "src_0001", "quote": "模型训练"},
            "field_evidence": [],
            "tech_stack": [],
            "highlights": [
                {
                    "value": "训练吞吐提升百分之二十。",
                    "evidence": {
                        "source_id": "src_0003",
                        "quote": "训练吞吐提升百分之二十。",
                    },
                }
            ],
        },
        {
            "entry_id": "proj_002",
            "name": "服务评测",
            "evidence": {"source_id": "src_0004", "quote": "服务评测"},
            "field_evidence": [],
            "tech_stack": [],
            "highlights": [
                {
                    "value": "完成实时服务评测。",
                    "evidence": {
                        "source_id": "src_0005",
                        "quote": "完成实时服务评测。",
                    },
                }
            ],
        },
    ]
    blocks = [
        {"source_id": "src_0001", "text": "模型训练"},
        {"source_id": "src_0002", "text": "基于 Megatron-LM 训练模型。"},
        {"source_id": "src_0003", "text": "训练吞吐提升百分之二十。"},
        {"source_id": "src_0004", "text": "服务评测"},
        {"source_id": "src_0005", "text": "完成实时服务评测。"},
    ]
    normalization = load_normalization_map(
        "resources/normalization/2.0/normalization_map.yaml"
    )

    violations = collect_taxonomy_skill_coverage_violations(
        payload,
        normalization,
        blocks,
    )
    plan = plan_local_repair(
        payload,
        "SemanticValidationError",
        violations,
        blocks,
    )

    assert plan is not None
    assert plan.targets == (
        RepairTarget("project_experience", 0),
        RepairTarget("project_experience", 1),
    )


def test_inline_work_entry_transitions_out_of_an_unheaded_project_section():
    payload = _raw_payload()
    work_heading = "◆ 后端工程师|示例科技|2023.10—至今"
    work_task = "使用 Python 开发数据接口并持续维护线上服务"
    declared_skill = "熟练使用 Python"
    work = payload["work_experience"][0]
    work.update(
        {
            "company": "示例科技",
            "position": "后端工程师",
            "date": {"start": "2023.10", "end": "至今"},
            "tech_stack": [
                {
                    "name": "Python",
                    "item_type": "programming_language",
                    "evidence": {"source_id": "src_0003", "quote": work_task},
                }
            ],
            "responsibilities": [
                {
                    "value": work_task,
                    "evidence": {"source_id": "src_0003", "quote": work_task},
                }
            ],
            "achievements": [],
            "evidence": {"source_id": "src_0002", "quote": work_heading},
            "field_evidence": [
                {
                    "field_name": field_name,
                    "evidence": {"source_id": "src_0002", "quote": work_heading},
                }
                for field_name in ("company", "position", "date")
            ],
        }
    )
    payload["skills"] = [
        {
            "name": "Python",
            "item_type": "programming_language",
            "proficiency": "proficient",
            "evidence": {"source_id": "src_0005", "quote": declared_skill},
        }
    ]
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(payload, "cv_inline_work")
    )
    blocks = [
        {"source_id": "src_0001", "text": "项目经历"},
        {"source_id": "src_0002", "text": work_heading},
        {"source_id": "src_0003", "text": work_task},
        {"source_id": "src_0004", "text": "Skills 专业技能"},
        {"source_id": "src_0005", "text": declared_skill},
    ]
    normalization = load_normalization_map("resources/normalization/2.0/normalization_map.yaml")

    assert collect_source_section_coverage_violations(result, blocks, normalization) == []
    assert collect_taxonomy_skill_coverage_violations(
        result, normalization, blocks
    ) == []


def test_source_coverage_requires_a_name_only_for_an_explicit_name_label():
    payload = populate_deterministic_fields(_raw_payload(), "cv_explicit_name")
    payload["personal_info"] = {
        "expected_position": "后端工程师",
        "evidence": {"source_id": "src_0002", "quote": "求职方向:后端工程师"},
        "field_evidence": [
            {
                "field_name": "expected_position",
                "evidence": {"source_id": "src_0002", "quote": "求职方向:后端工程师"},
            }
        ],
    }
    blocks = [
        {"source_id": "src_0001", "text": "候选人:测试姓名"},
        {"source_id": "src_0002", "text": "求职方向:后端工程师"},
    ]

    violations = collect_source_section_coverage_violations(payload, blocks)

    assert violations == [
        {
            "code": "explicit_personal_name_uncovered",
            "entry_id": "personal_info",
            "source_id": "src_0001",
            "source_text": "候选人:测试姓名",
        }
    ]
    plan = plan_local_repair(
        payload, "SemanticValidationError", violations, blocks
    )
    assert plan is not None
    assert plan.targets == (RepairTarget("personal_info"),)


def test_personal_location_rejects_institution_suffix() -> None:
    payload = _raw_payload()
    evidence = {
        "source_id": "src_0002",
        "quote": "所在地:示例省示例市示例大学",
    }
    payload["personal_info"] = {
        "current_location": "示例省示例市示例大学",
        "evidence": evidence,
        "field_evidence": [
            {"field_name": "current_location", "evidence": evidence}
        ],
    }
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(payload, "cv_location")
    )

    with pytest.raises(SemanticValidationError) as captured:
        validate_semantic_constraints(result)

    violation = next(
        item
        for item in captured.value.violations
        if item["code"] == "invalid_personal_location_shape"
    )
    assert violation["entry_id"] == "personal_info"


def test_taxonomy_source_coverage_allows_an_explicit_mapping_opt_out():
    payload = _raw_payload()
    payload["work_experience"][0]["tech_stack"] = []
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(payload, "cv_metric_context")
    )
    normalization = load_normalization_map("resources/normalization/2.0/normalization_map.yaml")
    blocks = [
        {"source_id": "src_0000", "text": "工作经历"},
        {
            "source_id": "src_0001",
            "text": "示例公司 开发工程师 使用 Python 开发服务并降低 CPU 开销",
        },
    ]

    violations = collect_taxonomy_skill_coverage_violations(
        result, normalization, blocks
    )

    assert any(item["name"] == "Python" for item in violations)
    assert not any(item["name"] == "CPU" for item in violations)


def test_taxonomy_source_coverage_preserves_case_distinct_skill_identity():
    normalization = load_normalization_map("resources/normalization/2.0/normalization_map.yaml")
    blocks = [
        {"source_id": "src_0001", "text": "专业技能"},
        {
            "source_id": "src_0002",
            "text": "熟悉ReAct工具调用循环与Prompt Chaining",
        },
    ]

    requirements = collect_source_taxonomy_requirements(normalization, blocks)

    assert any(item["name"] == "ReAct" for item in requirements)
    assert not any(item["name"] == "React" for item in requirements)


def test_taxonomy_source_coverage_does_not_match_inside_hyphenated_identity():
    normalization = load_normalization_map("resources/normalization/2.0/normalization_map.yaml")
    blocks = [
        {"source_id": "src_0001", "text": "项目经历"},
        {"source_id": "src_0002", "text": "技术栈:nano-vllm"},
    ]

    requirements = collect_source_taxonomy_requirements(normalization, blocks)

    assert not any(item["name"] == "vLLM" for item in requirements)


def test_taxonomy_source_coverage_treats_plus_as_stack_separator():
    normalization = load_normalization_map("resources/normalization/2.0/normalization_map.yaml")
    blocks = [
        {"source_id": "src_0001", "text": "项目经历"},
        {"source_id": "src_0002", "text": "技术栈:PyTorch+Pandas+NumPy"},
    ]

    requirements = collect_source_taxonomy_requirements(normalization, blocks)

    assert {item["name"] for item in requirements} >= {"PyTorch", "Pandas", "NumPy"}


def test_skill_evidence_treats_leading_hyphen_as_list_marker():
    normalization = load_normalization_map("resources/normalization/2.0/normalization_map.yaml")
    blocks = [
        {"source_id": "src_0001", "text": "-RoBERTa测试集准确率达到0.86"},
    ]
    payload = _raw_payload()
    payload["skills"] = [
        {
            "name": "RoBERTa",
            "item_type": "domain_knowledge",
            "evidence": {"source_id": "src_0001", "quote": "RoBERTa"},
        }
    ]
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(payload, "cv_list_marker_skill")
    )

    assert collect_skill_evidence_support_violations(result, normalization, blocks) == []


def test_semantic_gate_accepts_action_named_technical_artifact():
    payload = _raw_payload()
    evidence = {"source_id": "src_0001", "quote": "微调示例模型"}
    payload["project_experience"] = [
        {
            "name": "微调示例模型",
            "tech_stack": [],
            "highlights": [],
            "evidence": evidence,
            "field_evidence": [{"field_name": "name", "evidence": evidence}],
        }
    ]
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(payload, "cv_action_artifact")
    )

    validate_semantic_constraints(result)


def test_shared_source_block_does_not_cross_classify_credential_and_award() -> None:
    payload = _raw_payload()
    shared_evidence = {
        "source_id": "src_0001",
        "quote": "校二等奖学金、CET-6",
    }
    payload["certificates"] = [
        {
            "name": "CET-6",
            "kind": "language_certification",
            "evidence": shared_evidence,
        }
    ]
    payload["awards"] = [
        {
            "name": "校二等奖学金",
            "evidence": shared_evidence,
        }
    ]
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(payload, "cv_shared_credential_award")
    )

    validate_semantic_constraints(result)


@pytest.mark.parametrize(
    ("name", "quote", "expected_code"),
    [
        (
            "全国大学生人工智能创新大赛",
            "全国大学生人工智能创新大赛|省级二等奖",
            "award_name_missing_result",
        ),
        ("单项奖", "单项奖", "vague_award_name"),
    ],
)
def test_award_name_must_preserve_specific_result(
    name: str, quote: str, expected_code: str
) -> None:
    payload = _raw_payload()
    payload["awards"] = [
        {
            "name": name,
            "evidence": {"source_id": "src_0001", "quote": quote},
        }
    ]
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(payload, "cv_award_name_quality")
    )

    with pytest.raises(SemanticValidationError) as exc_info:
        validate_semantic_constraints(result)

    assert expected_code in {item["code"] for item in exc_info.value.violations}

def test_taxonomy_source_coverage_uses_all_long_alias_occurrences():
    normalization = load_normalization_map("resources/normalization/2.0/normalization_map.yaml")
    blocks = [
        {"source_id": "src_0001", "text": "专业技能"},
        {
            "source_id": "src_0002",
            "text": "使用AutoGen Studio设计流程并通过AutoGen Studio构建应用",
        },
    ]

    requirements = collect_source_taxonomy_requirements(normalization, blocks)

    assert any(item["name"] == "AutoGen Studio" for item in requirements)
    assert not any(item["name"] == "AutoGen" for item in requirements)


def test_taxonomy_source_coverage_ignores_repository_link_metadata():
    normalization = load_normalization_map("resources/normalization/2.0/normalization_map.yaml")
    blocks = [
        {"source_id": "src_0001", "text": "项目经历"},
        {"source_id": "src_0002", "text": "代码分析平台"},
        {"source_id": "src_0003", "text": "GitHub:https://example.invalid/repo"},
    ]

    assert collect_source_taxonomy_requirements(normalization, blocks) == []


@pytest.mark.parametrize(
    ("name", "source_text"),
    [
        ("React", "熟悉ReAct工具调用循环"),
        ("JS", "使用Auth.js完成认证"),
        ("BERT", "采用预训练语言模型完成分类"),
    ],
)
def test_skill_evidence_rejects_lexically_different_identity(
    name: str, source_text: str
) -> None:
    payload = _raw_payload()
    payload["work_experience"] = []
    payload["skills"] = [
        {
            "name": name,
            "item_type": {
                "React": "framework",
                "JS": "programming_language",
                "BERT": "domain_knowledge",
            }[name],
            "evidence": {"source_id": "src_0002", "quote": source_text},
        }
    ]
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(payload, "cv_skill_evidence")
    )
    normalization = load_normalization_map("resources/normalization/2.0/normalization_map.yaml")

    violations = collect_skill_evidence_support_violations(
        result,
        normalization,
        [{"source_id": "src_0002", "text": source_text}],
    )

    assert [item["code"] for item in violations] == [
        "skill_evidence_name_uncovered"
    ]


def test_skill_evidence_accepts_label_and_shared_slash_notation() -> None:
    payload = _raw_payload()
    payload["work_experience"] = []
    payload["skills"] = [
        {
            "name": "Redis",
            "item_type": "database",
            "evidence": {"source_id": "src_0002", "quote": "掌握持久化机制"},
        },
        {
            "name": "Vue3",
            "item_type": "framework",
            "evidence": {"source_id": "src_0003", "quote": "熟悉Vue2/3"},
        },
    ]
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(payload, "cv_skill_evidence")
    )
    normalization = load_normalization_map("resources/normalization/2.0/normalization_map.yaml")
    blocks = [
        {"source_id": "src_0002", "text": "Redis:掌握持久化机制"},
        {"source_id": "src_0003", "text": "熟悉Vue2/3并了解响应式原理"},
    ]

    assert collect_skill_evidence_support_violations(
        result, normalization, blocks
    ) == []


def test_composite_project_heading_keeps_uncovered_fact_and_taxonomy_in_one_repair():
    payload = populate_deterministic_fields(_raw_payload(), "cv_000001")
    payload["work_experience"] = []
    evidence = {"source_id": "src_0002", "quote": "无人机巡逻项目"}
    payload["project_experience"] = [
        {
            "entry_id": "proj_001",
            "name": "无人机巡逻项目",
            "evidence": evidence,
            "field_evidence": [{"field_name": "name", "evidence": evidence}],
            "tech_stack": [],
            "highlights": [],
        }
    ]
    blocks = [
        {"source_id": "src_0001", "text": "科研/项目经历"},
        {"source_id": "src_0002", "text": "无人机巡逻项目"},
        {
            "source_id": "src_0003",
            "text": "通过超声波传感器提升无人机机群环境感知能力并完成自主巡逻功能开发",
        },
    ]
    normalization = load_normalization_map("resources/normalization/2.0/normalization_map.yaml")

    violations = collect_source_section_coverage_violations(
        payload, blocks, normalization
    )
    uncovered = next(item for item in violations if item["source_id"] == "src_0003")

    assert uncovered["entry_id"] == "proj_001"
    assert uncovered["required_taxonomy_skills"] == [
        {
            "name": "超声波传感器",
            "skill_id": "TOOL_ULTRASONIC_SENSOR",
            "expected_item_type": "tool",
        }
    ]


def test_semantic_gate_rejects_competition_title_when_project_artifact_is_named():
    payload = _raw_payload()
    payload["project_experience"] = [
        {
            "name": "全国大学生计算机设计大赛",
            "tech_stack": [],
            "highlights": [
                {
                    "value": "设计并开发校园垃圾分类小程序后端系统",
                    "evidence": {
                        "source_id": "src_0002",
                        "quote": "设计并开发校园垃圾分类小程序后端系统",
                    },
                }
            ],
            "evidence": {"source_id": "src_0001", "quote": "全国大学生计算机设计大赛"},
            "field_evidence": [
                {
                    "field_name": "name",
                    "evidence": {"source_id": "src_0001", "quote": "全国大学生计算机设计大赛"},
                }
            ],
        }
    ]
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(payload, "cv_000001")
    )

    with pytest.raises(SemanticValidationError) as exc_info:
        validate_semantic_constraints(result)

    violation = next(
        item
        for item in exc_info.value.violations
        if item["code"] == "competition_title_as_project_name"
    )
    assert violation["entry_id"] == "proj_001"
    assert violation["artifact_source_ids"] == ["src_0002"]


def test_semantic_gate_rejects_competition_title_when_description_names_framework():
    payload = _raw_payload()
    payload["project_experience"] = [
        {
            "name": "全国大学生人工智能创新大赛",
            "description": {
                "value": "搭建模型训练框架并完成参数优化",
                "evidence": {
                    "source_id": "src_0002",
                    "quote": "搭建模型训练框架并完成参数优化",
                },
            },
            "tech_stack": [],
            "highlights": [],
            "evidence": {
                "source_id": "src_0001",
                "quote": "全国大学生人工智能创新大赛",
            },
            "field_evidence": [
                {
                    "field_name": "name",
                    "evidence": {
                        "source_id": "src_0001",
                        "quote": "全国大学生人工智能创新大赛",
                    },
                }
            ],
        }
    ]
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(payload, "cv_competition_description")
    )

    with pytest.raises(SemanticValidationError) as exc_info:
        validate_semantic_constraints(result)

    violation = next(
        item
        for item in exc_info.value.violations
        if item["code"] == "competition_title_as_project_name"
    )
    assert violation["artifact_source_ids"] == ["src_0002"]


def test_semantic_gate_moves_student_organization_roles_out_of_projects():
    payload = _raw_payload()
    payload["project_experience"] = [
        {
            "name": "新媒体中心",
            "role": "负责人",
            "tech_stack": [],
            "highlights": [],
            "evidence": {"source_id": "src_0001", "quote": "新媒体中心负责人"},
            "field_evidence": [
                {
                    "field_name": "name",
                    "evidence": {
                        "source_id": "src_0001",
                        "quote": "新媒体中心",
                    },
                },
                {
                    "field_name": "role",
                    "evidence": {"source_id": "src_0001", "quote": "负责人"},
                },
            ],
        }
    ]
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(payload, "cv_student_organization")
    )

    with pytest.raises(SemanticValidationError) as exc_info:
        validate_semantic_constraints(result)

    violation = next(
        item
        for item in exc_info.value.violations
        if item["code"] == "role_title_as_project"
    )
    assert violation["entry_id"] == "proj_001"
    assert violation["expected_collection"] == "work_experience"


def test_semantic_gate_rejects_project_ranking_duplicated_as_award():
    payload = _raw_payload()
    payload["awards"] = [
        {
            "name": "排名2/204",
            "evidence": {"source_id": "src_0001", "quote": "排名2/204"},
        }
    ]
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(payload, "cv_project_ranking")
    )

    with pytest.raises(SemanticValidationError) as exc_info:
        validate_semantic_constraints(result)

    violation = next(
        item
        for item in exc_info.value.violations
        if item["code"] == "project_metric_as_award"
    )
    assert violation["entry_id"] == "award_001"


def test_semantic_gate_rejects_outcome_phrase_as_project_identifier():
    payload = _raw_payload()
    payload["project_experience"] = [
        {
            "name": "模型推理表现",
            "tech_stack": [],
            "highlights": [],
            "evidence": {"source_id": "src_0001", "quote": "模型推理表现"},
            "field_evidence": [
                {
                    "field_name": "name",
                    "evidence": {
                        "source_id": "src_0001",
                        "quote": "模型推理表现",
                    },
                }
            ],
        }
    ]
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(payload, "cv_outcome_project_name")
    )

    with pytest.raises(SemanticValidationError) as exc_info:
        validate_semantic_constraints(result)

    violation = next(
        item
        for item in exc_info.value.violations
        if item["code"] == "invalid_project_name_shape"
    )
    assert violation["reasons"] == [
        "outcome_phrase_instead_of_project_identifier"
    ]


def test_preprocess_does_not_guess_the_first_column():
    cv_input, failure = preprocess_row({"unrelated": "not a resume"}, 1)
    assert cv_input is None
    assert failure["error_type"] == "missing_required_input"


class FakeClient:
    def __init__(self, model: str, timeout: int = 300, **_: object):
        self.model = model

    def extract(self, system_prompt: str, user_prompt: str) -> DeepSeekResult:
        payload = _raw_payload()
        raw = json.dumps(payload, ensure_ascii=False)
        return DeepSeekResult(data=payload, raw_response=raw)


class SectionShardFakeClient:
    def __init__(self, model: str, timeout: int = 300, **_: object):
        self.model = model

    def extract(self, system_prompt: str, user_prompt: str) -> DeepSeekResult:
        scope_line = user_prompt.split("requested_top_level_fields: ", 1)[1].splitlines()[0]
        fields = json.loads(scope_line)
        full_payload = _raw_payload()
        payload = {name: full_payload[name] for name in fields if name in full_payload}
        raw = json.dumps(payload, ensure_ascii=False)
        return DeepSeekResult(data=payload, raw_response=raw)


def test_http_section_shards_merge_before_whole_document_validation(monkeypatch):
    monkeypatch.setattr("src.pipeline.DeepSeekClient", SectionShardFakeClient)
    pipeline = CVExtractionPipeline(
        model="fake",
        normalization_path="resources/normalization/2.0/normalization_map.yaml",
        semantic_retry_attempts=0,
        parallel_section_extraction=True,
    )

    result = pipeline.extract_one(
        document_id="cv_section_shards",
        raw_text="示例公司 开发工程师 使用 Python 开发服务",
    )

    assert result.extraction.work_experience[0].company == "示例公司"
    assert result.extraction.skills[0].name == "Python"
    assert result.extraction.publications == []
    assert len(result.api_attempts) == 4
    assert {attempt["shard"] for attempt in result.api_attempts} == {
        "identity_history",
        "projects",
        "skills_credentials",
        "research_summary",
    }


class WrongTypeFakeClient:
    def __init__(self, model: str, timeout: int = 300, **_: object):
        self.model = model

    def extract(self, system_prompt: str, user_prompt: str) -> DeepSeekResult:
        payload = json.loads(
            json.dumps(_raw_payload(), ensure_ascii=False).replace(
                "Python", "Transformer"
            )
        )
        payload["skills"][0]["item_type"] = "framework"
        raw = json.dumps(payload, ensure_ascii=False)
        return DeepSeekResult(data=payload, raw_response=raw)


class PersonalRepairFakeClient:
    def __init__(self, model: str, timeout: int = 300, **_: object):
        self.model = model
        self.calls = 0

    def extract(self, system_prompt: str, user_prompt: str) -> DeepSeekResult:
        self.calls += 1
        if self.calls == 1:
            payload = _raw_payload()
            evidence = {"source_id": "src_0001", "quote": "开发工程师"}
            payload["personal_info"] = {
                "expected_position": "开发工程师",
                "evidence": evidence,
                "field_evidence": [],
            }
        else:
            assert '"singleton":"personal_info"' in user_prompt
            evidence = {"source_id": "src_0001", "quote": "开发工程师"}
            payload = {
                "operations": [
                    {
                        "op": "replace",
                        "target": {"singleton": "personal_info"},
                        "value": {
                            "expected_position": "开发工程师",
                            "evidence": evidence,
                            "field_evidence": [
                                {"field_name": "expected_position", "evidence": evidence}
                            ],
                        },
                    }
                ]
            }
        raw = json.dumps(payload, ensure_ascii=False)
        return DeepSeekResult(data=payload, raw_response=raw)


class FailedLocalRepairThenFullRetryFakeClient:
    def __init__(self, model: str, timeout: int = 300, **_: object):
        self.model = model
        self.calls = 0

    def extract(self, system_prompt: str, user_prompt: str) -> DeepSeekResult:
        self.calls += 1
        evidence = {"source_id": "src_0001", "quote": "开发工程师"}
        if self.calls == 1:
            payload = _raw_payload()
            payload["personal_info"] = {
                "expected_position": "开发工程师",
                "evidence": evidence,
                "field_evidence": [],
            }
        elif self.calls == 2:
            assert "# 局部校验修复任务" in user_prompt
            payload = {
                "operations": [
                    {
                        "op": "replace",
                        "target": {"singleton": "personal_info"},
                        "value": {
                            "expected_position": "开发工程师",
                            "evidence": evidence,
                            "field_evidence": [],
                        },
                    }
                ]
            }
        else:
            assert "# 局部校验修复任务" not in user_prompt
            assert "# 上一轮被拒绝的完整 JSON" in user_prompt
            payload = _raw_payload()
        raw = json.dumps(payload, ensure_ascii=False)
        return DeepSeekResult(data=payload, raw_response=raw)


class CoverageFirstFakeClient:
    def __init__(self, model: str, timeout: int = 300, **_: object):
        self.model = model
        self.calls = 0

    def extract(self, system_prompt: str, user_prompt: str) -> DeepSeekResult:
        self.calls += 1
        payload = _raw_payload()
        if self.calls == 1:
            payload["work_experience"][0]["tech_stack"] = [
                {
                    "name": "示例协议",
                    "item_type": "protocol",
                    "evidence": {"source_id": "src_0001", "quote": "开发服务"},
                }
            ]
        else:
            assert "source_section_uncovered" in user_prompt
            assert "# 局部校验修复任务" in user_prompt
            payload["work_experience"][0]["responsibilities"].append(
                {
                    "value": "负责用户反馈SDK重构并完成多个子应用接入与加载性能优化",
                    "evidence": {
                        "source_id": "src_0003",
                        "quote": "负责用户反馈SDK重构并完成多个子应用接入与加载性能优化",
                    },
                }
            )
            payload["work_experience"][0]["tech_stack"] = [
                {
                    "name": "性能优化",
                    "item_type": "methodology",
                    "evidence": {"source_id": "src_0003", "quote": "性能优化"},
                }
            ]
            payload = {
                "operations": [
                    {
                        "op": "replace",
                        "target": {"collection": "work_experience", "index": 0},
                        "value": payload["work_experience"][0],
                    }
                ]
            }
        raw = json.dumps(payload, ensure_ascii=False)
        return DeepSeekResult(data=payload, raw_response=raw)


def test_pipeline_writes_extraction_normalization_excel_and_consistent_report(monkeypatch):
    monkeypatch.setattr("src.pipeline.DeepSeekClient", FakeClient)
    work = Path("pytest_artifacts") / f"cv_v2_{uuid4().hex}"
    try:
        work.mkdir(parents=True)
        input_path = work / "input.csv"
        pd.DataFrame(
            [{"cv_id": "cv_000001", "简历原文": "示例公司 开发工程师 使用 Python 开发服务"}]
        ).to_csv(
            input_path, index=False
        )
        pipeline = CVExtractionPipeline(
            model="fake",
            normalization_path="resources/normalization/2.0/normalization_map.yaml",
            continue_on_error=False,
            run_id="v2",
            audit_sample_rate=0,
            max_workers=1,
            semantic_retry_attempts=0,
        )
        pipeline.run(str(input_path), str(work / "output"))
        run = work / "output" / "runs" / "v2"
        final = run / "final"
        assert (final / "annotations.jsonl").exists()
        assert (final / "normalized_annotations.jsonl").exists()
        assert (final / "normalized_annotations.json").exists()
        assert not (final / "skill_classifications.jsonl").exists()
        assert not (final / "skill_classifications.json").exists()
        normalized = json.loads(
            (final / "normalized_annotations.jsonl").read_text(encoding="utf-8")
        )
        skill = normalized["normalized_skills"][0]
        assert normalized["skill_taxonomy_version"] == "skill-taxonomy-snapshot.v1"
        assert skill["identity_resolution_status"] == "resolved"
        assert skill["classification_resolution_status"] == "resolved"
        assert skill["classifications"]
        assert "category_code" not in skill
        assert "subcategory_code" not in skill
        assert (final / "annotations.xlsx").exists()
        assert (final / "match_features.jsonl").exists()
        assert (final / "match_feature_profiles.json").exists()
        assert (final / "capability_verification_profiles.json").exists()
        assert (final / "capability_profiles.jsonl").exists()
        assert (final / "capability_evidence_links.jsonl").exists()
        summary = summarize_run(run)
        assert summary["manifest"]["total_rows"] == 1
        assert summary["counts"]["normalized_skills"] == 1
        assert summary["manifest"]["classification_occurrence_count"] == 1
        assert summary["manifest"]["classification_resolved_count"] == 1
        assert all(summary["integrity_checks"].values())
        report = (run / "research_report.md").read_text(encoding="utf-8")
        assert "本轮处理 1 份简历" in report
        assert "校验重试恢复 | 0" in report
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_pipeline_recovers_personal_info_with_singleton_local_repair(monkeypatch):
    monkeypatch.setattr("src.pipeline.DeepSeekClient", PersonalRepairFakeClient)
    work = Path("pytest_artifacts") / f"cv_personal_repair_{uuid4().hex}"
    try:
        work.mkdir(parents=True)
        input_path = work / "input.csv"
        pd.DataFrame(
            [{"cv_id": "cv_000001", "简历原文": "示例公司 开发工程师 使用 Python 开发服务"}]
        ).to_csv(input_path, index=False)
        pipeline = CVExtractionPipeline(
            model="fake",
            normalization_path="resources/normalization/2.0/normalization_map.yaml",
            continue_on_error=False,
            run_id="personal_repair",
            audit_sample_rate=0,
            max_workers=1,
            semantic_retry_attempts=1,
        )
        pipeline.run(str(input_path), str(work / "output"))
        manifest = json.loads(
            (work / "output" / "runs" / "personal_repair" / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        assert manifest["success_count"] == 1
        assert manifest["local_repair_count"] == 1
        assert manifest["local_repair_recovered_count"] == 1
        assert manifest["full_reextract_count"] == 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_pipeline_uses_full_reextract_after_one_unsuccessful_local_repair(monkeypatch):
    monkeypatch.setattr(
        "src.pipeline.DeepSeekClient", FailedLocalRepairThenFullRetryFakeClient
    )
    pipeline = CVExtractionPipeline(
        model="fake",
        normalization_path="resources/normalization/2.0/normalization_map.yaml",
        continue_on_error=False,
        max_workers=1,
        semantic_retry_attempts=2,
    )
    outcome = pipeline._extract_validated(
        {
            "cv_id": "cv_retry_strategy",
            "source_blocks": [
                {
                    "source_id": "src_0001",
                    "text": "示例公司 开发工程师 使用 Python 开发服务",
                    "start": 0,
                    "end": 27,
                }
            ],
        },
        "extract",
    )

    assert outcome.error is None
    assert outcome.annotation is not None
    assert outcome.local_repair_count == 1
    assert outcome.full_reextract_count == 1
    assert [attempt["mode"] for attempt in outcome.extraction_attempts] == [
        "initial",
        "local_repair",
        "full_reextract",
    ]


def test_pipeline_checks_global_source_coverage_before_local_schema_repair(monkeypatch):
    monkeypatch.setattr("src.pipeline.DeepSeekClient", CoverageFirstFakeClient)
    pipeline = CVExtractionPipeline(
        model="fake",
        normalization_path="resources/normalization/2.0/normalization_map.yaml",
        continue_on_error=False,
        max_workers=1,
        semantic_retry_attempts=1,
    )
    source_blocks = [
        {
            "source_id": "src_0001",
            "text": "示例公司 开发工程师 使用 Python 开发服务",
            "start": 0,
            "end": 29,
        },
        {"source_id": "src_0002", "text": "工作经历", "start": 30, "end": 34},
        {
            "source_id": "src_0003",
            "text": "负责用户反馈SDK重构并完成多个子应用接入与加载性能优化",
            "start": 35,
            "end": 65,
        },
    ]
    outcome = pipeline._extract_validated(
        {"cv_id": "cv_000001", "source_blocks": source_blocks},
        "extract",
    )

    assert outcome.error is None
    assert outcome.annotation is not None
    assert outcome.full_reextract_count == 0
    assert outcome.local_repair_count == 1
    assert outcome.extraction_attempts[0]["stage"] == "candidate_validation"
    issues = outcome.extraction_attempts[0]["error_details"]
    assert {issue["error_type"] for issue in issues} == {
        "SchemaValidationError",
        "SemanticValidationError",
    }
    coverage = next(
        issue for issue in issues if issue["error_type"] == "SemanticValidationError"
    )
    assert coverage["error_details"][0]["code"] == "source_section_uncovered"
    assert coverage["error_details"][0]["entry_id"] == "work_001"


def test_match_features_use_jd_taxonomy_and_keep_atomic_evidence():
    result = _validated_result()
    normalization = load_normalization_map("resources/normalization/2.0/normalization_map.yaml")
    normalized = normalize_extraction(result, normalization)
    profile = build_cv_match_features(
        result,
        normalized,
        normalization,
        as_of_date=pd.Timestamp("2026-07-18").date(),
    )
    skill = next(feature for feature in profile.features if feature.feature_type == "skill")
    task = next(feature for feature in profile.features if feature.feature_type == "task")
    assert skill.canonical_id == "LANG_PYTHON"
    assert skill.structured_values["aggregation_key"] == "canonical:LANG_PYTHON"
    assert skill.structured_values["occurrence_kind"] == "declared"
    assert skill.structured_values["proficiency_explicit"] is True
    assert skill.evidence_refs[0].quote == "使用 Python 开发服务"
    assert task.source_scope.endswith(":responsibility")
    assert task.vector_text == "开发工程师；使用 Python 开发服务"
    assert len(task.evidence_refs) == 2


def test_work_tech_stack_uses_the_same_normalization_and_match_contract():
    payload = _raw_payload()
    payload["work_experience"][0]["tech_stack"] = [
        {
            "name": "Python",
            "item_type": "programming_language",
            "proficiency": "unknown",
            "evidence": {"source_id": "src_0001", "quote": "使用 Python 开发服务"},
        }
    ]
    result = _validated_result_from_payload(payload)
    normalization = load_normalization_map("resources/normalization/2.0/normalization_map.yaml")
    normalized = normalize_extraction(result, normalization)
    profile = build_cv_match_features(
        result,
        normalized,
        normalization,
        as_of_date=pd.Timestamp("2026-07-18").date(),
    )
    work_skill = next(
        feature
        for feature in profile.features
        if feature.feature_type == "skill"
        and feature.structured_values["occurrence_kind"] == "work"
    )
    assert work_skill.canonical_id == "LANG_PYTHON"
    assert work_skill.source_scope == "work_experience:work_001:tech_stack"
    assert any(
        skill.source_item_id == work_skill.source_object_id
        and skill.source_scope == work_skill.source_scope
        for skill in normalized.normalized_skills
    )


def test_unknown_skill_proficiency_is_not_interpreted_as_candidate_level():
    result = _validated_result()
    result.skills[0].proficiency = "unknown"
    normalization = load_normalization_map("resources/normalization/2.0/normalization_map.yaml")
    normalized = normalize_extraction(result, normalization)
    profile = build_cv_match_features(
        result,
        normalized,
        normalization,
        as_of_date=pd.Timestamp("2026-07-18").date(),
    )
    skill = next(feature for feature in profile.features if feature.feature_type == "skill")
    assert skill.candidate_level is None
    assert skill.structured_values["proficiency_explicit"] is False


def test_unknown_language_and_award_levels_are_not_candidate_levels():
    payload = _raw_payload()
    evidence = {"source_id": "src_0001", "quote": "使用 Python 开发服务"}
    payload["languages"] = [
        {"language": "Python", "proficiency": "unknown", "evidence": evidence}
    ]
    payload["awards"][0]["level"] = "unknown"
    result = _validated_result_from_payload(payload)
    normalization = load_normalization_map("resources/normalization/2.0/normalization_map.yaml")
    profile = build_cv_match_features(
        result,
        normalize_extraction(result, normalization),
        normalization,
        as_of_date=pd.Timestamp("2026-07-18").date(),
    )
    language = next(feature for feature in profile.features if feature.feature_type == "language")
    award = next(feature for feature in profile.features if feature.feature_type == "award")
    assert language.candidate_level is None
    assert award.candidate_level is None


def test_project_role_remains_experience_context_not_job_role_feature():
    payload = _raw_payload()
    evidence = {
        "source_id": "src_0001",
        "quote": "示例公司 开发工程师 使用 Python 开发服务 检索项目 项目负责人",
    }
    payload["project_experience"] = [
        {
            "name": "检索项目",
            "role": "项目负责人",
            "tech_stack": [],
            "highlights": [],
            "evidence": evidence,
            "field_evidence": [
                {"field_name": "name", "evidence": evidence},
                {"field_name": "role", "evidence": evidence},
            ],
        }
    ]
    result = align_all_evidence(
        CVExtractionResult.model_validate(
            populate_deterministic_fields(payload, "cv_000001")
        ),
        [
            {
                "source_id": "src_0001",
                "text": "示例公司 开发工程师 使用 Python 开发服务 检索项目 项目负责人",
                "start": 0,
                "end": 36,
            }
        ],
    )
    normalization = load_normalization_map("resources/normalization/2.0/normalization_map.yaml")
    profile = build_cv_match_features(
        result,
        normalize_extraction(result, normalization),
        normalization,
        as_of_date=pd.Timestamp("2026-07-18").date(),
    )
    assert not any(
        feature.feature_type == "role" and feature.source_scope.startswith("project_experience:")
        for feature in profile.features
    )
    project = next(
        feature
        for feature in profile.features
        if feature.feature_type == "experience"
        and feature.source_scope.startswith("project_experience:")
    )
    assert project.structured_values["project_role"] == "项目负责人"


def test_extraction_stage_aligns_item_type_before_normalization():
    payload = _raw_payload()
    payload["skills"][0]["name"] = "Transformer"
    payload["skills"][0]["item_type"] = "framework"
    normalization = load_normalization_map("resources/normalization/2.0/normalization_map.yaml")
    canonicalized, corrections = canonicalize_authoritative_fields(payload, normalization)
    assert canonicalized["skills"][0]["item_type"] == "methodology"
    assert "item_id" not in canonicalized["skills"][0]
    assert corrections[0]["authority"] == "normalization_map"
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(canonicalized, "cv_000001")
    )
    normalized = normalize_extraction(result, normalization)
    skill = normalized.normalized_skills[0]
    assert skill.skill_id == "AI_TRANSFORMER"
    assert skill.category_code == "methodology"


def test_react_and_react_method_use_exact_case_before_type_correction():
    normalization = load_normalization_map("resources/normalization/2.0/normalization_map.yaml")
    payload = _raw_payload()
    payload["skills"] = [
        {**payload["skills"][0], "name": "React", "item_type": "library"},
        {**payload["skills"][0], "name": "ReAct", "item_type": "library"},
    ]
    canonicalized, corrections = canonicalize_authoritative_fields(payload, normalization)
    assert [item["item_type"] for item in canonicalized["skills"]] == [
        "framework", "methodology"
    ]
    assert len(corrections) == 2
    assert lookup_skill_mapping(normalization, "react", "framework") is None
    assert lookup_skill_mapping(normalization, "react", "methodology") is None


def test_high_frequency_cv_skill_aliases_use_the_shared_jd_taxonomy():
    normalization = load_normalization_map("resources/normalization/2.0/normalization_map.yaml")
    expected = {
        ("Tailwind", "framework"): "FRAMEWORK_TAILWIND_CSS",
        ("ECharts", "library"): "LIBRARY_ECHARTS",
        ("Express", "framework"): "FRAMEWORK_EXPRESS",
        ("SVM", "methodology"): "METHOD_SVM",
        ("ES6+", "programming_language"): "LANG_JAVASCRIPT",
        ("Pinia", "library"): "LIBRARY_PINIA",
        ("Axios", "library"): "LIBRARY_AXIOS",
        ("C3", "programming_language"): "LANG_CSS",
        ("SQLite", "database"): "DATABASE_SQLITE",
        ("Three.js", "library"): "LIBRARY_THREE_JS",
        ("JMeter", "tool"): "TOOL_JMETER",
        ("Element", "library"): "LIBRARY_ELEMENT_UI",
        ("Qwen-7B", "domain_knowledge"): "AI_QWEN",
        ("Web Component", "domain_knowledge"): "KNOWLEDGE_WEB_COMPONENTS",
        ("RAG会话检索", "methodology"): "AI_RAG",
        ("Skills声明式加载", "methodology"): "METHOD_DECLARATIVE_SKILL_LOADING",
        ("Task级监控埋点", "methodology"): "METHOD_TASK_OBSERVABILITY_INSTRUMENTATION",
        ("GLSL", "programming_language"): "LANG_GLSL",
        ("MNIST", "domain_knowledge"): "KNOWLEDGE_MNIST",
        ("雪崩", "domain_knowledge"): "KNOWLEDGE_CACHE_AVALANCHE",
        ("击穿", "domain_knowledge"): "KNOWLEDGE_CACHE_BREAKDOWN",
        ("CrowS-Pairs", "domain_knowledge"): "KNOWLEDGE_CROWS_PAIRS",
        ("Python爬虫", "methodology"): "METHOD_PYTHON_WEB_SCRAPING",
        ("YAML", "tool"): "TOOL_YAML",
        ("JS设计模式", "domain_knowledge"): "KNOWLEDGE_JAVASCRIPT_DESIGN_PATTERNS",
        ("LLM Post-training", "methodology"): "METHOD_LLM_POST_TRAINING",
        ("VLM Post-training", "methodology"): "METHOD_VLM_POST_TRAINING",
        ("GitLab CI/CD", "tool"): "TOOL_GITLAB_CI",
        ("CI", "methodology"): "METHOD_CONTINUOUS_INTEGRATION",
        ("Try-Lock", "methodology"): "METHOD_TRY_LOCK",
        ("Event Camera", "tool"): "TOOL_EVENT_CAMERA",
        ("RFID技术", "domain_knowledge"): "KNOWLEDGE_RFID",
        ("Linux开发", "platform"): "PLATFORM_LINUX",
        ("Qwen2.5-1.5B", "domain_knowledge"): "AI_QWEN",
        ("Vue.js框架", "framework"): "FRAMEWORK_VUE",
        ("WebSocket通信协议", "tool"): "TOOL_WEBSOCKET",
        ("Flink SQL", "programming_language"): "LANG_SQL",
        ("YOLOv5", "methodology"): "METHOD_YOLO",
        ("YOLO目标检测模型", "methodology"): "METHOD_YOLO",
        ("YAML frontmatter", "tool"): "TOOL_YAML",
        ("Promise", "domain_knowledge"): "KNOWLEDGE_JAVASCRIPT_PROMISE",
        ("L3Bv2-I", "domain_knowledge"): "AI_L3BV2_I",
        ("Electron", "framework"): "FRAMEWORK_ELECTRON",
        ("NPM", "tool"): "TOOL_NPM",
        ("SSR", "methodology"): "METHOD_SERVER_SIDE_RENDERING",
        ("AST", "domain_knowledge"): "KNOWLEDGE_ABSTRACT_SYNTAX_TREE",
        ("ESBuild", "tool"): "TOOL_ESBUILD",
        ("Grunt", "tool"): "TOOL_GRUNT",
        ("AntV L7", "library"): "LIB_ANTV_L7",
        ("IPC", "domain_knowledge"): "KNOWLEDGE_INTER_PROCESS_COMMUNICATION",
        ("JSX", "programming_language"): "LANG_JSX",
        ("LLM Inference", "methodology"): "METHOD_LLM_INFERENCE",
        ("BatGPT", "domain_knowledge"): "AI_BATGPT",
        ("Linux系统", "platform"): "PLATFORM_LINUX",
        ("集成方法", "methodology"): "METHOD_ENSEMBLE_LEARNING",
        ("特征工程方法", "methodology"): "METHOD_FEATURE_ENGINEERING",
        ("PCA降维", "methodology"): "METHOD_PCA",
        ("数据预处理", "methodology"): "METHOD_DATA_PREPROCESSING",
        ("DINO", "methodology"): "METHOD_DINO",
        ("MAE", "methodology"): "METHOD_MASKED_AUTOENCODER",
    }
    assert {
        key: lookup_skill_mapping(normalization, *key)["skill_id"]
        for key in expected
    } == expected


def test_deterministic_field_stage_does_not_rewrite_business_content():
    payload = {
        "skills": [{"name": "Vue2/3", "item_type": "framework"}],
        "project_experience": [{"name": "采用SFT提升模型表现", "tech_stack": []}],
    }
    populated = populate_deterministic_fields(payload, "cv_001")
    assert populated["skills"][0]["name"] == "Vue2/3"
    assert populated["project_experience"][0]["name"] == "采用SFT提升模型表现"


def test_evidence_canonicalization_trims_cross_block_model_continuation():
    payload = {
        "skills": [],
        "project_experience": [{
            "description": {
                "value": "第一段。第二段。",
                "evidence": {"source_id": "src_0001", "quote": "第一段。第二段。"},
            }
        }],
    }
    canonicalized, corrections = canonicalize_evidence_quotes(
        payload, [{"source_id": "src_0001", "text": "第一段。"}]
    )
    assert canonicalized["project_experience"][0]["description"]["evidence"]["quote"] == "第一段。"
    assert corrections[0]["authority"] == "source_block_containment"


def test_project_required_name_evidence_repair_is_explicit_in_both_retry_prompts():
    errors = [{
        "code": "invalid_match_field_evidence",
        "entry_id": "proj_001",
        "type": "project_experience",
        "missing_fields": [],
        "duplicate_fields": [],
        "unexpected_fields": [],
        "unsupported_fields": ["name"],
    }, {
        "code": "invalid_project_name_shape",
        "entry_id": "proj_001",
        "name": "• 搭建在线商城系统。",
        "reasons": ["leading_list_marker", "sentence_ending_punctuation"],
    }]
    blocks = [{"source_id": "src_0001", "text": "在线商城系统"}]
    local_prompt = build_local_repair_prompt(
        "SemanticValidationError", errors,
        [{"collection": "project_experience", "index": 0, "value": {}}], blocks,
    )
    retry_prompt = build_validation_retry_prompt(
        {"cv_id": "cv_000001", "source_blocks": blocks},
        [],
        [],
        "SemanticValidationError", errors,
    )
    for prompt in (local_prompt, retry_prompt):
        assert "project_experience.name" in prompt
        assert "不得只删除其 field_evidence 后保留原值" in prompt
        assert "课程项目 -" in prompt
        assert "只有原文根本不支持该对象时才删除整个对象" in prompt
        assert "不得删除整个项目" in prompt
        assert "最短且可独立识别该项目" in prompt


def test_outcome_only_project_repair_requires_removing_unrepresentable_object():
    errors = [
        {
            "code": "invalid_project_name_shape",
            "entry_id": "proj_001",
            "name": "示例模型表现",
            "reasons": ["outcome_phrase_instead_of_project_identifier"],
        }
    ]
    prompt = build_local_repair_prompt(
        "SemanticValidationError",
        errors,
        [{"collection": "project_experience", "index": 0, "value": {}}],
        [{"source_id": "src_0001", "text": "采用示例方法提升模型表现"}],
    )

    assert "必须删除整个项目对象" in prompt
    assert "不得把“模型/技能+水平/表现/效果/能力”截短后伪造项目名称" in prompt


def test_prompt_and_validator_enforce_jd_item_type_before_success():
    normalization = load_normalization_map("resources/normalization/2.0/normalization_map.yaml")
    source_blocks = [
        {"source_id": "src_0001", "text": "专业技能"},
        {"source_id": "src_0002", "text": "熟悉 Python"},
    ]
    requirements = collect_source_taxonomy_requirements(normalization, source_blocks)
    coverage_requirements = collect_source_coverage_requirements(source_blocks)
    prompt = build_user_prompt(
        {
            "cv_id": "cv_000001",
            "source_blocks": source_blocks,
        },
        requirements,
        coverage_requirements,
    )
    assert "jd_skill_type_contract" not in prompt
    assert "normalization_map" not in prompt
    assert requirements == [
        {
            "source_id": "src_0002",
            "section": "skills",
            "name": "Python",
            "item_type": "programming_language",
        }
    ]
    assert coverage_requirements == []

    payload = _raw_payload()
    payload["skills"][0]["name"] = "Transformer"
    payload["skills"][0]["item_type"] = "framework"
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(payload, "cv_000001")
    )
    with pytest.raises(SemanticValidationError, match="skill_item_type_mismatch"):
        validate_skill_item_type_contract(result, normalization)


def test_pipeline_canonicalizes_authoritative_type_before_success(monkeypatch):
    monkeypatch.setattr("src.pipeline.DeepSeekClient", WrongTypeFakeClient)
    work = Path("pytest_artifacts") / f"cv_authoritative_{uuid4().hex}"
    try:
        work.mkdir(parents=True)
        input_path = work / "input.csv"
        pd.DataFrame(
            [
                {
                    "cv_id": "cv_000001",
                    "简历原文": "示例公司 开发工程师 使用 Transformer 开发服务",
                }
            ]
        ).to_csv(
            input_path, index=False
        )
        pipeline = CVExtractionPipeline(
            model="fake",
            normalization_path="resources/normalization/2.0/normalization_map.yaml",
            continue_on_error=False,
            run_id="authoritative",
            audit_sample_rate=0,
            max_workers=1,
            semantic_retry_attempts=0,
        )
        pipeline.run(str(input_path), str(work / "output"))
        run = work / "output" / "runs" / "authoritative"
        annotation = json.loads(
            (run / "final" / "annotations.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )
        manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
        audit_record = json.loads(next((run / "audit").glob("*.json")).read_text(encoding="utf-8"))
        assert annotation["skills"][0]["item_type"] == "methodology"
        assert manifest["deterministic_correction_count"] == 1
        assert manifest["extraction_schema_version"] == "2.4"
        assert manifest["match_feature_derivation_version"] == "1.3"
        assert audit_record["deterministic_corrections"][0]["authority"] == "normalization_map"
        assert "jd_skill_type_contract" not in audit_record["user_prompt"]
        assert not (run / "final" / "skill_type_alignments.jsonl").exists()
    finally:
        shutil.rmtree(work, ignore_errors=True)
