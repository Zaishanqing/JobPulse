from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src import models as contract_models
from src.deduplicator import deduplicate_extraction
from src.deterministic_fields import canonicalize_authoritative_fields, populate_deterministic_fields
from src.exceptions import SemanticValidationError
from src.field_contract import (
    ALIGNMENT_VALUES,
    DEGREE_LEVEL_VALUES,
    EDUCATION_FIELD_EVIDENCE_NAME_VALUES,
    FIELD_CONTRACT_VERSION,
    PERSONAL_FIELD_EVIDENCE_NAME_VALUES,
    PROJECT_FIELD_EVIDENCE_NAME_VALUES,
    PROFICIENCY_VALUES,
    RESOLUTION_STATUS_VALUES,
    SKILL_ITEM_TYPE_VALUES,
    WORK_FIELD_EVIDENCE_NAME_VALUES,
    WORK_STATUS_VALUES,
    WORK_TYPE_VALUES,
)
from src.local_repair import LocalRepairPlan, RepairTarget, apply_local_repair, plan_local_repair
from src.models import CVExtractionResult
from src.normalizer import load_normalization_map
from src.review_rules import get_soft_review_issue_types
from src.skill_semantics import split_taxonomy_confirmed_shared_skill_name
from src.validator import split_composite_skill_name, validate_semantic_constraints


def test_external_contract_enums_match_python_contract():
    contract = yaml.safe_load(
        Path("config/field_contract.yaml").read_text(encoding="utf-8")
    )
    enums = contract["canonical_enums"]
    assert enums["degree_level"] == list(DEGREE_LEVEL_VALUES)
    assert enums["skill_item_type"] == list(SKILL_ITEM_TYPE_VALUES)
    assert enums["proficiency"] == list(PROFICIENCY_VALUES)
    assert enums["alignment"] == list(ALIGNMENT_VALUES)
    assert enums["resolution_status"] == list(RESOLUTION_STATUS_VALUES)
    assert enums["work_type"] == list(WORK_TYPE_VALUES)
    assert enums["work_status"] == list(WORK_STATUS_VALUES)
    assert contract["version"] == FIELD_CONTRACT_VERSION
    assert enums["personal_field_evidence_name"] == list(PERSONAL_FIELD_EVIDENCE_NAME_VALUES)
    assert enums["education_field_evidence_name"] == list(EDUCATION_FIELD_EVIDENCE_NAME_VALUES)
    assert enums["work_field_evidence_name"] == list(WORK_FIELD_EVIDENCE_NAME_VALUES)
    assert enums["project_field_evidence_name"] == list(PROJECT_FIELD_EVIDENCE_NAME_VALUES)
    assert get_soft_review_issue_types() == {
        "missing_personal_info",
        "missing_name",
        "missing_education",
        "missing_experience",
        "duplicate_skill",
        "unknown_skill_proficiency",
        "skill_item_other_requires_review",
        "unresolved_skill_normalization",
    }


def test_external_contract_model_fields_match_python_models() -> None:
    contract = yaml.safe_load(
        Path("config/field_contract.yaml").read_text(encoding="utf-8")
    )
    mismatches: dict[str, dict[str, list[str]]] = {}
    for model_name, model_contract in contract["models"].items():
        model = getattr(contract_models, model_name)
        declared_fields = set(model_contract["fields"])
        runtime_fields = set(model.model_fields)
        if declared_fields != runtime_fields:
            mismatches[model_name] = {
                "contract_only": sorted(declared_fields - runtime_fields),
                "runtime_only": sorted(runtime_fields - declared_fields),
            }

    assert mismatches == {}


def test_shared_affix_composite_split_requires_every_atomic_taxonomy_identity() -> None:
    known = {"静态检查", "动态检查"}

    assert split_taxonomy_confirmed_shared_skill_name(
        "静态与动态检查", known.__contains__
    ) == ["静态检查", "动态检查"]
    assert split_taxonomy_confirmed_shared_skill_name(
        "静态与未知检查", known.__contains__
    ) is None


@pytest.mark.parametrize(
    ("name", "input_type", "expected_type", "expected_skill_id"),
    [
        ("HTTP(S)", "other", "domain_knowledge", "KNOWLEDGE_HTTP"),
        ("AES", "other", "methodology", "METHOD_AES"),
        ("Canvas", "other", "domain_knowledge", "KNOWLEDGE_HTML_CANVAS"),
        ("React 18", "framework", "framework", "FRAMEWORK_REACT"),
        ("ROS Noetic", "framework", "framework", "FRAMEWORK_ROS"),
        ("Pydantic", "library", "library", "LIBRARY_PYDANTIC"),
        ("Airflow", "tool", "framework", "FRAMEWORK_APACHE_AIRFLOW"),
        ("OpenTelemetry", "tool", "tool", "TOOL_OPENTELEMETRY"),
        ("MinIO", "database", "platform", "PLATFORM_MINIO"),
        ("A/B实验", "methodology", "methodology", "METHOD_AB_TESTING"),
        ("Vue 3", "framework", "framework", "FRAMEWORK_VUE"),
        ("网络基础", "domain_knowledge", "domain_knowledge", "KNOWLEDGE_COMPUTER_NETWORKS"),
        ("浏览器性能分析", "tool", "methodology", "METHOD_BROWSER_PERFORMANCE_ANALYSIS"),
        ("ACL", "tool", "domain_knowledge", "KNOWLEDGE_ACCESS_CONTROL_LIST"),
        ("RRF", "methodology", "methodology", "METHOD_RECIPROCAL_RANK_FUSION"),
        ("JSON Schema", "methodology", "domain_knowledge", "KNOWLEDGE_JSON_SCHEMA"),
        ("消息队列基础", "domain_knowledge", "domain_knowledge", "KNOWLEDGE_MESSAGE_QUEUE"),
        ("Great Expectations", "tool", "tool", "TOOL_GREAT_EXPECTATIONS"),
        ("类型标注", "methodology", "methodology", "METHOD_TYPE_ANNOTATION"),
        ("测试", "methodology", "methodology", "METHOD_SOFTWARE_TESTING"),
        ("数据质量", "domain_knowledge", "domain_knowledge", "KNOWLEDGE_DATA_QUALITY"),
        ("OpenSearch", "database", "database", "DATABASE_OPENSEARCH"),
        ("Fluent Bit", "tool", "tool", "TOOL_FLUENT_BIT"),
        ("Envoy", "tool", "tool", "TOOL_ENVOY"),
        ("eBPF", "methodology", "domain_knowledge", "KNOWLEDGE_EBPF"),
        ("OCR", "methodology", "domain_knowledge", "KNOWLEDGE_OPTICAL_CHARACTER_RECOGNITION"),
        ("HSV", "methodology", "domain_knowledge", "KNOWLEDGE_HSV_COLOR_SPACE"),
        ("JMH", "tool", "tool", "TOOL_JMH"),
        ("RBAC", "methodology", "domain_knowledge", "KNOWLEDGE_ROLE_BASED_ACCESS_CONTROL"),
        ("灰度标签", "methodology", "methodology", "METHOD_CANARY_LABELING"),
        ("审计记录", "methodology", "methodology", "METHOD_AUDIT_LOGGING"),
        ("敏感值掩码", "methodology", "methodology", "METHOD_SENSITIVE_VALUE_MASKING"),
    ],
)
def test_shared_jd_taxonomy_covers_stable_cv_terms(
    name: str,
    input_type: str,
    expected_type: str,
    expected_skill_id: str,
) -> None:
    normalization = load_normalization_map("resources/normalization/2.0/normalization_map.yaml")
    payload = {
        "skills": [
            {
                "name": name,
                "item_type": input_type,
                "evidence": {"source_id": "src_0001", "quote": name},
            }
        ]
    }

    canonicalized, _ = canonicalize_authoritative_fields(payload, normalization)

    assert canonicalized["skills"][0]["item_type"] == expected_type
    assert normalization["skills"][name]["skill_id"] == expected_skill_id


def test_experience_unknown_proficiency_is_removed_deterministically() -> None:
    normalization = load_normalization_map("resources/normalization/2.0/normalization_map.yaml")
    payload = {
        "work_experience": [
            {
                "tech_stack": [
                    {
                        "name": "Python",
                        "item_type": "programming_language",
                        "proficiency": "unknown",
                    }
                ]
            }
        ]
    }

    canonicalized, corrections = canonicalize_authoritative_fields(payload, normalization)

    assert canonicalized["work_experience"][0]["tech_stack"][0]["proficiency"] is None
    assert corrections[0]["authority"] == "experience_proficiency_contract"


@pytest.mark.parametrize(
    ("quote", "input_level", "expected_level"),
    [
        ("全国大学生计算机设计大赛校级二等奖", "national", "school"),
        ("中国高校计算机大赛区域赛三等奖", "provincial", "other"),
        ("华为ICT大赛省级二等奖", None, "provincial"),
    ],
)
def test_award_level_uses_last_explicit_evidence_scope(
    quote: str,
    input_level: str | None,
    expected_level: str,
) -> None:
    normalization = load_normalization_map(
        "resources/normalization/2.0/normalization_map.yaml"
    )
    payload = {
        "awards": [
            {
                "name": quote,
                "level": input_level,
                "evidence": {"source_id": "src_0001", "quote": quote},
            }
        ]
    }

    canonicalized, corrections = canonicalize_authoritative_fields(
        payload, normalization
    )

    assert canonicalized["awards"][0]["level"] == expected_level
    assert corrections == [
        {
            "path": "awards[0].level",
            "from": input_level,
            "to": expected_level,
            "authority": "award_level_evidence",
        }
    ]


@pytest.mark.parametrize(
    ("name", "quote", "input_level", "expected_level"),
    [
        (
            "全国大学生建模竞赛浙江省三等奖",
            "全国大学生建模竞赛浙江省三等奖",
            "national",
            "provincial",
        ),
        (
            "国家级励志奖学金一等奖",
            "国家级励志奖学金一等奖、校级优秀奖学金二等奖",
            "school",
            "national",
        ),
        (
            "海外大学生建模竞赛特等奖提名",
            "海外大学生建模竞赛特等奖提名",
            "national",
            None,
        ),
    ],
)
def test_award_level_prefers_object_name_and_clears_unsupported_inference(
    name: str,
    quote: str,
    input_level: str | None,
    expected_level: str | None,
) -> None:
    normalization = load_normalization_map(
        "resources/normalization/2.0/normalization_map.yaml"
    )
    payload = {
        "awards": [
            {
                "name": name,
                "level": input_level,
                "evidence": {"source_id": "src_0001", "quote": quote},
            }
        ]
    }

    canonicalized, corrections = canonicalize_authoritative_fields(
        payload, normalization
    )

    assert canonicalized["awards"][0]["level"] == expected_level
    assert corrections == [
        {
            "path": "awards[0].level",
            "from": input_level,
            "to": expected_level,
            "authority": "award_level_evidence",
        }
    ]


@pytest.mark.parametrize(
    ("name", "expected_level"),
    [
        ("国家励志奖学金", "national"),
        ("浙江省政府奖学金", "provincial"),
        ("天津市大学生电子设计竞赛二等奖", "municipal"),
        ("北京交通大学研究生二等奖学金", "school"),
        ("校二等奖学金", "school"),
        ("省三好学生标兵", "provincial"),
    ],
)
def test_award_level_recognizes_explicit_named_scope(
    name: str, expected_level: str
) -> None:
    normalization = load_normalization_map(
        "resources/normalization/2.0/normalization_map.yaml"
    )
    canonicalized, _ = canonicalize_authoritative_fields(
        {
            "awards": [
                {
                    "name": name,
                    "evidence": {"source_id": "src_0001", "quote": name},
                }
            ]
        },
        normalization,
    )

    assert canonicalized["awards"][0]["level"] == expected_level


def test_award_level_does_not_leak_from_another_item_in_shared_evidence() -> None:
    normalization = load_normalization_map(
        "resources/normalization/2.0/normalization_map.yaml"
    )
    canonicalized, _ = canonicalize_authoritative_fields(
        {
            "awards": [
                {
                    "name": "砺学系列本科生特等奖学金",
                    "level": "school",
                    "evidence": {
                        "source_id": "src_0001",
                        "quote": "浙江省政府奖学金、校一等奖学金、砺学系列本科生特等奖学金",
                    },
                }
            ]
        },
        normalization,
    )

    assert canonicalized["awards"][0]["level"] is None


def test_duplicate_award_scope_wording_is_rejected_without_merging_stages() -> None:
    payload = {
        "personal_info": None,
        "education": [],
        "work_experience": [],
        "project_experience": [],
        "skills": [],
        "languages": [],
        "certificates": [],
        "awards": [
            {
                "name": "大学生服务创新大赛全国三等奖",
                "evidence": {
                    "source_id": "src_0001",
                    "quote": "大学生服务创新大赛全国三等奖",
                },
            },
            {
                "name": "全国大学生服务创新大赛国家三等奖",
                "evidence": {
                    "source_id": "src_0002",
                    "quote": "项目成果:国家三等奖",
                },
            },
            {
                "name": "全国大学生服务创新大赛区域二等奖",
                "evidence": {
                    "source_id": "src_0002",
                    "quote": "项目成果:区域二等奖",
                },
            },
        ],
        "self_evaluation": [],
    }
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(payload, "cv_award_duplicate")
    )

    with pytest.raises(SemanticValidationError) as captured:
        validate_semantic_constraints(result)

    assert captured.value.violations == [
        {
            "code": "duplicate_entry_semantics",
            "type": "awards",
            "entry_id": "award_002",
            "duplicate_of_entry_id": "award_001",
            "name": "全国大学生服务创新大赛国家三等奖",
        }
    ]


def test_unsupported_language_level_is_canonicalized_to_unknown() -> None:
    normalization = load_normalization_map(
        "resources/normalization/2.0/normalization_map.yaml"
    )
    payload = {
        "languages": [
            {
                "language": "英语",
                "proficiency": "professional",
                "evidence": {
                    "source_id": "src_0001",
                    "quote": "能够阅读英文论文并复现实验。",
                },
            }
        ]
    }

    canonicalized, corrections = canonicalize_authoritative_fields(
        payload, normalization
    )

    assert canonicalized["languages"][0]["proficiency"] == "unknown"
    assert corrections == [
        {
            "path": "languages[0].proficiency",
            "from": "professional",
            "to": "unknown",
            "authority": "language_proficiency_evidence",
        }
    ]


def test_local_repair_localizes_nested_skill_evidence_to_owning_project():
    payload = {
        "project_experience": [
            {
                "entry_id": "proj_001",
                "evidence": {"source_id": "src_0001", "quote": "项目A"},
                "tech_stack": [
                    {
                        "item_id": "skill_001",
                        "evidence": {"source_id": "src_0002", "quote": "Python"},
                    }
                ],
            }
        ]
    }
    plan = plan_local_repair(
        payload,
        "SourceBindingError",
        {
            "object_path": "project_experience[0].tech_stack[0]",
            "source_id": "src_0002",
        },
        [
            {"source_id": "src_0001", "text": "项目A"},
            {"source_id": "src_0002", "text": "Python"},
        ],
    )
    assert plan is not None
    assert [(target.collection, target.index) for target in plan.targets] == [
        ("project_experience", 0)
    ]
    assert {block["source_id"] for block in plan.source_blocks} == {"src_0001", "src_0002"}


def test_local_repair_materializes_all_schema_default_collections():
    payload = {
        "education": [{"school": "某大学", "major": "计算机", "degree": "unknown"}],
    }
    response = {
        "operations": [
            {
                "op": "replace",
                "target": {"collection": "education", "index": 0},
                "value": {"school": "某大学", "major": "计算机", "degree": "bachelor"},
            }
        ]
    }
    repaired = apply_local_repair(
        payload,
        response,
        LocalRepairPlan(targets=(RepairTarget("education", 0),), source_blocks=()),
    )
    assert repaired["education"][0]["degree"] == "bachelor"
    assert repaired["certificates"] == []
    assert repaired["project_experience"] == []


def test_local_repair_rejects_present_non_list_collection():
    payload = {"education": [], "certificates": None}
    response = {
        "operations": [
            {
                "op": "append",
                "target": {"collection": "education"},
                "value": {"school": "某大学", "major": "计算机", "degree": "bachelor"},
            }
        ]
    }
    try:
        apply_local_repair(
            payload,
            response,
            LocalRepairPlan(targets=(), source_blocks=(), append_collections=("education",)),
        )
    except ValueError as exc:
        assert "invalid certificates collection" in str(exc)
    else:
        raise AssertionError("Expected an invalid present collection to be rejected")


def test_composite_skill_split_preserves_shared_numeric_version_prefix():
    assert split_composite_skill_name("Vue2/3") == ["Vue2", "Vue3"]
    assert split_composite_skill_name("CUDA 11/12") == ["CUDA 11", "CUDA 12"]
    assert split_composite_skill_name("TensorFlow/PyTorch") == ["TensorFlow", "PyTorch"]
    assert split_composite_skill_name("SQLite/FTS5") == ["SQLite", "FTS5"]
    assert split_composite_skill_name("短期/长期记忆分层") is None
    assert split_composite_skill_name("CI/CD") is None
    assert split_composite_skill_name("GitLab CI/CD") is None


def test_deduplication_removes_same_skill_with_different_evidence_in_one_scope():
    payload = {
        "skills": [
            {
                "name": "Vue2", "item_type": "framework",
                "evidence": {"source_id": "src_0001", "quote": "Vue2"},
            },
            {
                "name": "Vue2", "item_type": "framework",
                "evidence": {"source_id": "src_0002", "quote": "Vue2/3"},
            },
        ],
        "project_experience": [{
            "name": "前端项目",
            "tech_stack": [{
                "name": "Vue2", "item_type": "framework",
                "evidence": {"source_id": "src_0003", "quote": "Vue2"},
            }],
            "highlights": [],
            "evidence": {"source_id": "src_0003", "quote": "前端项目"},
            "field_evidence": [{
                "field_name": "name",
                "evidence": {"source_id": "src_0003", "quote": "前端项目"},
            }],
        }],
    }
    result = CVExtractionResult.model_validate(
        populate_deterministic_fields(payload, "cv_000001")
    )
    deduplicated = deduplicate_extraction(result)
    assert [item.name for item in deduplicated.skills] == ["Vue2"]
    assert [item.name for item in deduplicated.project_experience[0].tech_stack] == ["Vue2"]


def test_top_level_composite_skill_repair_requires_exact_append_count():
    payload = {
        "skills": [
            {
                "item_id": "skill_001",
                "name": "TCP/UDP",
                "item_type": "domain_knowledge",
                "evidence": {"source_id": "src_0001", "quote": "TCP/UDP"},
            }
        ]
    }
    plan = plan_local_repair(
        payload,
        "SemanticValidationError",
        {
            "code": "composite_skill_item",
            "item_id": "skill_001",
            "parts": ["TCP", "UDP"],
        },
        [{"source_id": "src_0001", "text": "TCP/UDP"}],
    )
    assert plan is not None
    assert plan.append_collections == ("skills",)
    assert plan.required_append_counts == (("skills", 1),)
    response = {
        "operations": [
            {
                "op": "replace",
                "target": {"collection": "skills", "index": 0},
                "value": {
                    "name": "TCP",
                    "item_type": "domain_knowledge",
                    "evidence": {"source_id": "src_0001", "quote": "TCP"},
                },
            },
            {
                "op": "append",
                "target": {"collection": "skills"},
                "value": {
                    "name": "UDP",
                    "item_type": "domain_knowledge",
                    "evidence": {"source_id": "src_0001", "quote": "UDP"},
                },
            },
        ]
    }
    repaired = apply_local_repair(payload, response, plan)
    assert [item["name"] for item in repaired["skills"]] == ["TCP", "UDP"]

    with pytest.raises(ValueError, match="requires exactly 1 append"):
        apply_local_repair(payload, {"operations": response["operations"][:1]}, plan)


def test_misclassified_award_repair_requires_a_cross_collection_move() -> None:
    payload = {
        "certificates": [
            {
                "entry_id": "cert_001",
                "name": "算法竞赛一等奖",
                "kind": "competition_award",
                "evidence": {"source_id": "src_0001", "quote": "算法竞赛一等奖"},
            }
        ]
    }
    detail = {
        "code": "credential_award_misclassified",
        "entry_id": "cert_001",
        "source_collection": "certificates",
        "expected_collection": "awards",
        "source_id": "src_0001",
    }
    plan = plan_local_repair(
        payload,
        "SemanticValidationError",
        detail,
        [{"source_id": "src_0001", "text": "算法竞赛一等奖"}],
    )

    assert plan is not None
    assert plan.targets == (RepairTarget("certificates", 0),)
    assert plan.append_collections == ("awards",)
    assert plan.required_append_counts == (("awards", 1),)

    repaired = apply_local_repair(
        payload,
        {
            "operations": [
                {
                    "op": "remove",
                    "target": {"collection": "certificates", "index": 0},
                },
                {
                    "op": "append",
                    "target": {"collection": "awards"},
                    "value": {
                        "name": "算法竞赛一等奖",
                        "evidence": {
                            "source_id": "src_0001",
                            "quote": "算法竞赛一等奖",
                        },
                    },
                },
            ]
        },
        plan,
    )
    assert repaired["certificates"] == []
    assert [item["name"] for item in repaired["awards"]] == ["算法竞赛一等奖"]


def test_uncovered_language_capability_authorizes_one_language_append() -> None:
    text = "可进行英文技术文档阅读。"
    detail = {
        "code": "explicit_language_uncovered",
        "language": "English",
        "source_id": "src_0001",
        "source_text": text,
        "suggested_append_collection": "languages",
    }

    plan = plan_local_repair(
        {"languages": []},
        "SemanticValidationError",
        detail,
        [{"source_id": "src_0001", "text": text}],
    )

    assert plan is not None
    assert plan.targets == ()
    assert plan.append_collections == ("languages",)
    assert plan.required_append_counts == (("languages", 1),)


def test_research_project_misclassified_as_work_requires_collection_move() -> None:
    payload = {
        "work_experience": [
            {
                "entry_id": "work_001",
                "company": "示例大学实验室",
                "evidence": {"source_id": "src_0001", "quote": "博士课题"},
            }
        ]
    }
    detail = {
        "code": "research_project_as_work",
        "entry_id": "work_001",
        "source_collection": "work_experience",
        "expected_collection": "project_experience",
        "source_id": "src_0001",
    }

    plan = plan_local_repair(
        payload,
        "SemanticValidationError",
        detail,
        [{"source_id": "src_0001", "text": "博士课题"}],
    )

    assert plan is not None
    assert plan.targets == (RepairTarget("work_experience", 0),)
    assert plan.append_collections == ("project_experience",)
    assert plan.required_append_counts == (("project_experience", 1),)


def test_schema_error_can_plan_and_apply_personal_info_singleton_repair():
    payload = {
        "personal_info": {
            "expected_position": "开发工程师",
            "evidence": {"source_id": "src_0001", "quote": "开发工程师"},
            "field_evidence": [],
        }
    }
    plan = plan_local_repair(
        payload,
        "SchemaValidationError",
        [{"loc": ["personal_info"], "type": "value_error"}],
        [{"source_id": "src_0001", "text": "开发工程师"}],
    )
    assert plan is not None
    assert plan.targets == (RepairTarget("personal_info"),)
    response = {
        "operations": [
            {
                "op": "replace",
                "target": {"singleton": "personal_info"},
                "value": {
                    "expected_position": "开发工程师",
                    "evidence": {"source_id": "src_0001", "quote": "开发工程师"},
                    "field_evidence": [
                        {
                            "field_name": "expected_position",
                            "evidence": {"source_id": "src_0001", "quote": "开发工程师"},
                        }
                    ],
                },
            }
        ]
    }
    repaired = apply_local_repair(payload, response, plan)
    assert repaired["personal_info"]["expected_position"] == "开发工程师"
    assert repaired["skills"] == []

    with pytest.raises(ValueError, match="append is allowed only"):
        apply_local_repair(
            payload,
            {
                "operations": [
                    {
                        "op": "append",
                        "target": {"singleton": "personal_info"},
                        "value": response["operations"][0]["value"],
                    }
                ]
            },
            plan,
        )


def test_missing_personal_info_can_be_replaced_by_bounded_name_repair() -> None:
    text = "测试姓名 目标岗位:后端工程师"
    detail = {
        "code": "explicit_personal_name_uncovered",
        "entry_id": "personal_info",
        "source_id": "src_0001",
        "source_text": text,
    }
    payload = {"personal_info": None}
    plan = plan_local_repair(
        payload,
        "SemanticValidationError",
        detail,
        [{"source_id": "src_0001", "text": text}],
    )

    assert plan is not None
    assert plan.targets == (RepairTarget("personal_info"),)
    repaired = apply_local_repair(
        payload,
        {
            "operations": [
                {
                    "op": "replace",
                    "target": {"singleton": "personal_info"},
                    "value": {
                        "name": "测试姓名",
                        "expected_position": "后端工程师",
                        "evidence": {"source_id": "src_0001", "quote": text},
                        "field_evidence": [
                            {
                                "field_name": "expected_position",
                                "evidence": {
                                    "source_id": "src_0001",
                                    "quote": "后端工程师",
                                },
                            }
                        ],
                    },
                }
            ]
        },
        plan,
    )
    assert repaired["personal_info"]["name"] == "测试姓名"


def test_skill_evidence_identity_repair_includes_same_owner_candidate_source() -> None:
    payload = {
        "project_experience": [
            {
                "entry_id": "project_001",
                "name": "文本分类项目",
                "evidence": {"source_id": "src_0001", "quote": "文本分类项目"},
                "tech_stack": [
                    {
                        "item_id": "skill_001",
                        "name": "BERT",
                        "item_type": "tool",
                        "evidence": {
                            "source_id": "src_0001",
                            "quote": "预训练语言模型",
                        },
                    }
                ],
            }
        ]
    }
    detail = {
        "code": "skill_evidence_name_uncovered",
        "item_id": "skill_001",
        "name": "BERT",
        "source_id": "src_0001",
        "candidate_source_ids": ["src_0002"],
    }
    plan = plan_local_repair(
        payload,
        "SemanticValidationError",
        detail,
        [
            {"source_id": "src_0001", "text": "使用预训练语言模型"},
            {"source_id": "src_0002", "text": "技术栈：BERT"},
            {"source_id": "src_0003", "text": "无关内容"},
        ],
    )

    assert plan is not None
    assert plan.targets == (RepairTarget("project_experience", 0),)
    assert tuple(block["source_id"] for block in plan.source_blocks) == (
        "src_0001",
        "src_0002",
    )


def test_duplicate_award_repair_targets_the_duplicate_object() -> None:
    payload = {
        "awards": [
            {
                "entry_id": "award_001",
                "name": "全国算法竞赛三等奖",
                "evidence": {
                    "source_id": "src_0001",
                    "quote": "全国算法竞赛三等奖",
                },
            },
            {
                "entry_id": "award_002",
                "name": "算法竞赛全国三等奖",
                "evidence": {
                    "source_id": "src_0002",
                    "quote": "算法竞赛全国三等奖",
                },
            },
        ]
    }
    plan = plan_local_repair(
        payload,
        "SemanticValidationError",
        {
            "code": "duplicate_entry_semantics",
            "type": "awards",
            "entry_id": "award_002",
            "duplicate_of": "award_001",
            "source_id": "src_0002",
        },
        [
            {"source_id": "src_0001", "text": "全国算法竞赛三等奖"},
            {"source_id": "src_0002", "text": "算法竞赛全国三等奖"},
        ],
    )

    assert plan is not None
    assert plan.targets == (RepairTarget("awards", 1),)
    assert tuple(block["source_id"] for block in plan.source_blocks) == ("src_0002",)
