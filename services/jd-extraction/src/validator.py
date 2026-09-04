from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from pydantic import ValidationError

from .exceptions import InvalidJSONError, SchemaValidationError, SemanticValidationError
from .models import (
    EducationRequirement,
    ExperienceRequirement,
    JDExtractionResult,
    JDNormalizedResult,
    OtherRequirement,
    SkillRequirement,
    SoftSkillRequirement,
)
from .review_rules import get_review_rule
from .deterministic_fields import infer_employment_kind
from .normalizer import lookup_skill_mapping, skill_mapping_candidates
from .salary_parser import SALARY_PATTERN, is_standalone_salary_expression

BUSINESS_VALIDATOR_VERSION = "3.9"

RESPONSIBILITY_SECTION_HEADINGS = ("岗位职责", "职位职责", "工作职责")
SECTION_HEADINGS = (
    *RESPONSIBILITY_SECTION_HEADINGS,
    "任职要求",
    "职位要求",
    "岗位要求",
    "公司介绍",
    "企业介绍",
    "工作地点",
    "薪资",
)

RETRYABLE_REVIEW_ISSUES = {
    "duplicate_skill_in_requirement",
    "empty_structured_constraint",
    "duplicate_requirement_semantics",
    "conflicting_requirement_modality",
    "overlapping_requirement_evidence",
    "skill_item_other_requires_review",
}

EMPLOYMENT_ATOMIC_CUE_GROUPS = (
    ("五险一金", "六险二金", "社会保险", "公积金"),
    ("年终奖", "项目奖金", "绩效奖金"),
    ("股票期权", "股权", "期权"),
    ("带薪年假", "生日假"),
    ("年度体检", "定期体检"),
    ("交通补助", "交通补贴", "通讯补贴"),
    ("员工旅游", "团建聚餐", "定期团建"),
    ("零食下午茶", "免费午餐", "餐补"),
)

CANDIDATE_CUES_IN_EMPLOYMENT = ("优先", "仅招", "学历不限", "经验不限", "应届毕业生可投")
COMPANY_CUES_IN_EMPLOYMENT = ("显卡资源", "GPU 集群", "GPU集群", "算力资源", "API无限调用", "API 无上限调用", "团队来自", "团队成员来自")
CANDIDATE_CUES_IN_COMPANY = (
    "你将",
    "你会",
    "你可以",
    "候选人",
    "应聘者",
    "发展通道",
    "晋升通道",
    "职业发展",
    "可转正",
    "转正编",
    "领导nice",
)
CANDIDATE_REQUIREMENT_ACTION_PREFIXES = ("熟悉", "掌握", "精通", "具备", "会使用", "能够使用", "了解")
TECHNICAL_CAPABILITY_CUES = (
    "熟悉",
    "掌握",
    "精通",
    "会使用",
    "能够使用",
    "熟练使用",
    "熟练运用",
    "熟练掌握",
)
TECHNICAL_ENTITY_CUES = (
    "软件",
    "工具",
    "系统",
    "平台",
    "引擎",
    "框架",
    "语言",
    "协议",
    "数据库",
    "算法",
    "模型",
    "Office",
    "Excel",
    "PowerPoint",
    "PPT",
)
TRAINING_CUES = ("培训", "导师", "课程", "培养", "指导", "技术分享")
COMPOSITE_SKILL_ALLOWLIST = {
    "CI/CD",
    "TCP/IP",
    "I/O",
    "I/O网络编程",
    "I/O 网络编程",
    "A/B测试",
    "A/B 测试",
    "A/B实验",
    "A/B 实验",
}
DESCRIPTIVE_SKILL_SUFFIXES = ("能力", "技术栈", "策略设计")
DESCRIPTIVE_SKILL_FRAGMENTS = ("相关场景", "相关产品或框架")
PLATFORM_ARTIFACTS = ("来自BOSS直聘", "kanzhun")
BOSS_ZHIPIN_BRAND = "BOSS直聘"
LEGAL_BOSS_TERMS = (BOSS_ZHIPIN_BRAND, "BOSS主页")
INSERTED_BOSS_PATTERN = re.compile(
    r"(?:(?<=[\u4e00-\u9fff])boss|boss(?=[\u4e00-\u9fff]))",
    re.IGNORECASE,
)


def _contains_inserted_boss_artifact(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value)
    folded = normalized.casefold()
    legal_terms = tuple(term.casefold() for term in LEGAL_BOSS_TERMS)
    for match in INSERTED_BOSS_PATTERN.finditer(normalized):
        if any(folded.startswith(term, match.start()) for term in legal_terms):
            continue
        return True
    return False


def _semantic_strings(value: Any, path: str = "") -> list[tuple[str, str]]:
    if isinstance(value, dict):
        strings: list[tuple[str, str]] = []
        for key, item in value.items():
            if key in {"evidence", "requirement_id", "fact_id"}:
                continue
            child_path = f"{path}.{key}" if path else key
            strings.extend(_semantic_strings(item, child_path))
        return strings
    if isinstance(value, list):
        strings = []
        for index, item in enumerate(value):
            strings.extend(_semantic_strings(item, f"{path}[{index}]"))
        return strings
    return [(path, value)] if isinstance(value, str) else []


def validate_schema(data: dict) -> JDExtractionResult:
    if not isinstance(data, dict):
        raise InvalidJSONError("Model output must be a JSON object")
    try:
        return JDExtractionResult.model_validate(data)
    except ValidationError as exc:
        raise SchemaValidationError(
            f"Schema validation failed: {exc}",
            errors=exc.errors(include_url=False),
        ) from exc


def validate_semantic_constraints(result: JDExtractionResult) -> None:
    violations: list[dict[str, Any]] = []
    seen_facts: dict[tuple[str, str, str], str] = {}
    title_source_id = result.job_title.evidence.source_id if result.job_title is not None else None
    title_quote = result.job_title.evidence.quote if result.job_title is not None else None
    for responsibility in result.responsibilities:
        action = responsibility.action.strip()
        if (
            title_source_id is not None
            and responsibility.evidence.source_id == title_source_id
            and responsibility.evidence.quote == title_quote
            and action == result.job_title.value.strip()
        ):
            violations.append(
                {
                    "code": "job_title_duplicate_responsibility",
                    "requirement_id": responsibility.requirement_id,
                    "action": responsibility.action,
                }
            )
        if action.startswith(CANDIDATE_REQUIREMENT_ACTION_PREFIXES):
            violations.append(
                {
                    "code": "candidate_requirement_in_responsibility",
                    "requirement_id": responsibility.requirement_id,
                    "action": responsibility.action,
                }
            )
    education_by_degree: dict[str, list[EducationRequirement]] = {}
    for requirement in result.requirements:
        if isinstance(requirement, EducationRequirement) and requirement.minimum_degree not in (None, "unknown"):
            education_by_degree.setdefault(requirement.minimum_degree, []).append(requirement)
    for degree, requirements in education_by_degree.items():
        for left_index, left in enumerate(requirements):
            for right in requirements[left_index + 1 :]:
                if left.modality == right.modality:
                    continue
                pair = (left, right)
                has_explicit_preference = any(
                    requirement.modality == "preferred" and "优先" in requirement.evidence.quote
                    for requirement in pair
                )
                has_generic_degree_tag = any(
                    requirement.modality == "required"
                    and ("学历:" in requirement.evidence.quote or "学历：" in requirement.evidence.quote)
                    for requirement in pair
                )
                if has_explicit_preference and has_generic_degree_tag:
                    violations.append(
                        {
                            "code": "conflicting_education_degree_modality",
                            "minimum_degree": degree,
                            "requirement_ids": [left.requirement_id, right.requirement_id],
                        }
                    )
    for scope, facts in (("company", result.company_facts), ("employment", result.employment_facts)):
        for fact in facts:
            normalized_value = " ".join(unicodedata.normalize("NFKC", fact.value).casefold().split())
            key = (scope, fact.kind, normalized_value)
            related_fact_id = seen_facts.get(key)
            if related_fact_id is not None:
                violations.append(
                    {
                        "code": "duplicate_fact_semantics",
                        "scope": scope,
                        "fact_id": fact.fact_id,
                        "related_fact_id": related_fact_id,
                        "value": fact.value,
                    }
                )
            else:
                seen_facts[key] = fact.fact_id
    semantic_objects: list[tuple[str, str | None, Any]] = []
    if result.job_title is not None:
        semantic_objects.append(("job_title", None, result.job_title.model_dump()))
    semantic_objects.extend(
        ("responsibility", item.requirement_id, item.model_dump())
        for item in result.responsibilities
    )
    semantic_objects.extend(
        ("requirement", item.requirement_id, item.model_dump())
        for item in result.requirements
    )
    semantic_objects.extend(
        ("company_fact", item.fact_id, item.model_dump())
        for item in result.company_facts
    )
    semantic_objects.extend(
        ("employment_fact", item.fact_id, item.model_dump())
        for item in result.employment_facts
    )
    company_names = {
        unicodedata.normalize("NFKC", fact.value).strip().casefold()
        for fact in result.company_facts
        if fact.kind == "company_name"
    }
    boss_zhipin_is_employer = BOSS_ZHIPIN_BRAND.casefold() in company_names
    for object_type, object_id, payload in semantic_objects:
        for field_path, value in _semantic_strings(payload):
            is_company_name = object_type == "company_fact" and field_path == "value" and any(
                fact.fact_id == object_id and fact.kind == "company_name"
                for fact in result.company_facts
            )
            artifacts = [artifact for artifact in PLATFORM_ARTIFACTS if artifact.casefold() in value.casefold()]
            if not boss_zhipin_is_employer and BOSS_ZHIPIN_BRAND.casefold() in value.casefold():
                artifacts.append(BOSS_ZHIPIN_BRAND)
            if (
                not is_company_name
                and re.search(r"(?<=[\u4e00-\u9fff])直聘(?=[\u4e00-\u9fff])", value)
            ):
                artifacts.append("直聘（词中插入）")
            if (
                not is_company_name
                and _contains_inserted_boss_artifact(value)
            ):
                artifacts.append("boss（中文词内插入）")
            if not artifacts:
                continue
            violation: dict[str, Any] = {
                "code": "platform_artifact_in_semantic_value",
                "object_type": object_type,
                "field_path": field_path,
                "value": value,
                "artifacts": artifacts,
            }
            if object_type in {"responsibility", "requirement"}:
                violation["requirement_id"] = object_id
            elif object_type in {"company_fact", "employment_fact"}:
                violation["fact_id"] = object_id
            violations.append(violation)

    for fact in result.employment_facts:
        if is_standalone_salary_expression(fact.value):
            violations.append(
                {
                    "code": "base_salary_in_employment_fact",
                    "fact_id": fact.fact_id,
                    "kind": fact.kind,
                    "value": fact.value,
                }
            )
        matched_groups = [group for group in EMPLOYMENT_ATOMIC_CUE_GROUPS if any(cue in fact.value for cue in group)]
        if len(matched_groups) >= 2:
            violations.append(
                {
                    "code": "non_atomic_employment_fact",
                    "fact_id": fact.fact_id,
                    "kind": fact.kind,
                    "value": fact.value,
                    "matched_group_count": len(matched_groups),
                }
            )
        if fact.kind == "other" and any(cue in fact.value for cue in CANDIDATE_CUES_IN_EMPLOYMENT):
            violations.append(
                {
                    "code": "candidate_constraint_in_employment_fact",
                    "fact_id": fact.fact_id,
                    "value": fact.value,
                }
            )
        if fact.kind == "other" and any(cue in fact.value for cue in COMPANY_CUES_IN_EMPLOYMENT):
            violations.append(
                {
                    "code": "company_fact_in_employment_fact",
                    "fact_id": fact.fact_id,
                    "value": fact.value,
                }
            )
        if fact.kind == "training" and not any(cue in fact.value for cue in TRAINING_CUES):
            violations.append(
                {
                    "code": "employment_kind_evidence_mismatch",
                    "fact_id": fact.fact_id,
                    "kind": fact.kind,
                    "value": fact.value,
                }
            )
        expected_kind = infer_employment_kind(fact.value.strip())
        if expected_kind is not None and fact.kind != expected_kind:
            violations.append(
                {
                    "code": "employment_kind_evidence_mismatch",
                    "fact_id": fact.fact_id,
                    "kind": fact.kind,
                    "expected_kind": expected_kind,
                    "value": fact.value,
                }
            )

    for fact in result.company_facts:
        if fact.kind == "other" and any(cue in fact.value for cue in CANDIDATE_CUES_IN_COMPANY):
            violations.append(
                {
                    "code": "candidate_facing_company_fact",
                    "fact_id": fact.fact_id,
                    "value": fact.value,
                }
            )

    for requirement in result.requirements:
        if isinstance(requirement, OtherRequirement):
            semantic_text = " ".join(
                value
                for value in (
                    requirement.label,
                    requirement.value,
                    requirement.evidence.quote,
                )
                if isinstance(value, str)
            )
            folded_text = semantic_text.casefold()
            if (
                any(cue in semantic_text for cue in TECHNICAL_CAPABILITY_CUES)
                and any(cue.casefold() in folded_text for cue in TECHNICAL_ENTITY_CUES)
            ):
                violations.append(
                    {
                        "code": "technical_requirement_in_other",
                        "requirement_id": requirement.requirement_id,
                        "label": requirement.label,
                        "value": requirement.value,
                    }
                )
        if isinstance(requirement, SoftSkillRequirement):
            normalized_skills = [
                " ".join(unicodedata.normalize("NFKC", skill).casefold().split())
                for skill in requirement.skills
            ]
            if len(normalized_skills) != len(set(normalized_skills)):
                violations.append(
                    {
                        "code": "duplicate_soft_skill_in_requirement",
                        "requirement_id": requirement.requirement_id,
                        "skills": requirement.skills,
                    }
                )
        if not isinstance(requirement, SkillRequirement):
            continue
        for item in requirement.items:
            if item.name.endswith(DESCRIPTIVE_SKILL_SUFFIXES) or any(
                fragment in item.name for fragment in DESCRIPTIVE_SKILL_FRAGMENTS
            ):
                violations.append(
                    {
                        "code": "descriptive_skill_item",
                        "requirement_id": requirement.requirement_id,
                        "name": item.name,
                        "item_type": item.item_type,
                    }
                )
            if "经验" in item.name:
                violations.append(
                    {
                        "code": "experience_phrase_in_skill_item",
                        "requirement_id": requirement.requirement_id,
                        "name": item.name,
                        "item_type": item.item_type,
                    }
                )
            if "/" in item.name and item.name not in COMPOSITE_SKILL_ALLOWLIST:
                parts = [part.strip() for part in item.name.split("/") if part.strip()]
                if len(parts) >= 2:
                    violations.append(
                        {
                            "code": "composite_skill_item",
                            "requirement_id": requirement.requirement_id,
                            "name": item.name,
                            "parts": parts,
                        }
                    )
            parenthetical_match = re.fullmatch(r".+[（(]([^（）()]+)等[）)]", item.name)
            if parenthetical_match:
                examples = [
                    part.strip()
                    for part in re.split(r"[/、,，]", parenthetical_match.group(1))
                    if part.strip()
                ]
                violations.append(
                    {
                        "code": "category_with_parenthetical_examples_in_skill_item",
                        "requirement_id": requirement.requirement_id,
                        "name": item.name,
                        "examples": examples,
                    }
                )
            if item.name.endswith("和") or item.name.startswith("或"):
                violations.append(
                    {
                        "code": "dangling_conjunction_skill_item",
                        "requirement_id": requirement.requirement_id,
                        "name": item.name,
                    }
                )
    if violations:
        raise SemanticValidationError(
            "Deterministic semantic validation failed: "
            + json.dumps(violations, ensure_ascii=False, separators=(",", ":")),
            violations=violations,
        )


def validate_explicit_section_completeness(
    result: JDExtractionResult,
    source_blocks: list[dict[str, Any]],
) -> None:
    """Reject an omitted responsibility collection only for an explicit section."""

    if result.responsibilities:
        return
    texts = [unicodedata.normalize("NFKC", str(block.get("text", ""))).strip() for block in source_blocks]
    responsibility_source_ids: list[str] = []
    for index, text in enumerate(texts):
        for heading in RESPONSIBILITY_SECTION_HEADINGS:
            if text == heading or text == f"{heading}:":
                if index + 1 < len(texts) and not any(
                    texts[index + 1].startswith(section) for section in SECTION_HEADINGS
                ):
                    responsibility_source_ids.append(str(source_blocks[index + 1].get("source_id", "")))
                break
            for separator in (":", " "):
                prefix = f"{heading}{separator}"
                if text.startswith(prefix) and text[len(prefix) :].strip():
                    responsibility_source_ids.append(str(source_blocks[index].get("source_id", "")))
                    break
            else:
                continue
            break
    if responsibility_source_ids:
        raise SemanticValidationError(
            "An explicit responsibility section was omitted from responsibilities.",
            violations=[
                {
                    "code": "missing_explicit_responsibilities",
                    "source_ids": responsibility_source_ids,
                }
            ],
        )


def validate_skill_item_type_contract(
    result: JDExtractionResult,
    normalization_map: dict[str, Any],
) -> None:
    violations: list[dict[str, Any]] = []
    for requirement in result.requirements:
        if not isinstance(requirement, SkillRequirement):
            continue
        for item in requirement.items:
            if lookup_skill_mapping(normalization_map, item.name, item.item_type) is not None:
                continue
            mappings = skill_mapping_candidates(normalization_map, item.name)
            expected_types = sorted({
                mapping.get("category_code") for mapping in mappings
                if isinstance(mapping, dict) and isinstance(mapping.get("category_code"), str)
            })
            if expected_types:
                violation = {
                    "code": "skill_item_type_mismatch",
                    "requirement_id": requirement.requirement_id,
                    "name": item.name,
                    "item_type": item.item_type,
                }
                if len(expected_types) == 1:
                    violation["expected_item_type"] = expected_types[0]
                else:
                    violation["expected_item_types"] = expected_types
                violations.append(violation)
    if violations:
        raise SemanticValidationError(
            "Skill item types conflict with the normalization taxonomy: "
            + json.dumps(violations, ensure_ascii=False, separators=(",", ":")),
            violations=violations,
        )


def reject_retryable_review_flags(
    flags: list[dict[str, Any]],
    source_blocks: list[dict[str, Any]] | None = None,
) -> None:
    source_texts = [str(block.get("text", "")) for block in (source_blocks or [])]

    def first_block_is_job_title() -> bool:
        if len(source_texts) < 2:
            return False
        text = source_texts[0].strip()
        if not text or len(text) > 80 or SALARY_PATTERN.search(text):
            return False
        if any(text.startswith(prefix) for prefix in (
            "岗位职责", "职位职责", "工作职责", "任职要求", "职位要求", "岗位要求",
            "公司介绍", "企业介绍", "工作地点", "薪资", "职位描述", "岗位描述",
        )):
            return False
        return not any(mark in text for mark in ("。", "；", ";"))

    def third_block_is_company_name() -> bool:
        if len(source_texts) < 3 or SALARY_PATTERN.search(source_texts[1]) is None:
            return False
        text = source_texts[2].strip()
        if not text or len(text) > 60:
            return False
        return not any(cue in text for cue in ("...", "…", "名字", "保密", "某公司"))

    retryable = [
        flag
        for flag in flags
        if flag.get("issue_type") in RETRYABLE_REVIEW_ISSUES
        or (flag.get("issue_type") == "missing_job_title" and first_block_is_job_title())
        or (flag.get("issue_type") == "missing_company_name" and third_block_is_company_name())
    ]
    if retryable:
        raise SemanticValidationError(
            "Retryable business validation issues: "
            + json.dumps(retryable, ensure_ascii=False, separators=(",", ":")),
            violations=retryable,
        )


def collect_illegal_enum_cases(
    data: dict[str, Any], errors: list[dict[str, Any]], document_id: str, row_index: int
) -> list[dict[str, Any]]:
    cases = []
    for error in errors:
        if error.get("type") != "literal_error":
            continue
        location = list(error.get("loc", ()))
        value: Any = data
        try:
            for part in location:
                value = value[part]
        except (KeyError, IndexError, TypeError):
            value = error.get("input")
        cases.append(
            {
                "jd_id": document_id,
                "row_index": row_index,
                "field_path": ".".join(str(part) for part in location),
                "field": str(location[-1]) if location else "",
                "raw_value": value,
                "allowed_values": re.findall(r"'([^']+)'", error.get("ctx", {}).get("expected", "")),
                "error_message": error.get("msg", ""),
            }
        )
    return cases


def _flag(
    result: JDExtractionResult,
    requirement_id: str,
    issue_type: str,
    quote: str,
    **details: Any,
) -> dict[str, Any]:
    rule = get_review_rule(issue_type)
    flag = {
        "jd_id": result.document_id,
        "requirement_id": requirement_id,
        "item_id": "",
        "issue_type": issue_type,
        "severity": rule["severity"],
        "rule_scope": rule["scope"],
        "issue_description": rule["description"],
        "raw_text": quote,
        "suggested_action": rule["suggested_action"],
    }
    flag.update(details)
    return flag


def _scoped_flag(
    document_id: str,
    issue_type: str,
    raw_text: str,
    *,
    requirement_id: str = "",
    item_id: str = "",
    **details: Any,
) -> dict[str, Any]:
    rule = get_review_rule(issue_type)
    flag = {
        "jd_id": document_id,
        "requirement_id": requirement_id,
        "item_id": item_id,
        "issue_type": issue_type,
        "severity": rule["severity"],
        "rule_scope": rule["scope"],
        "issue_description": rule["description"],
        "raw_text": raw_text,
        "suggested_action": rule["suggested_action"],
    }
    flag.update(details)
    return flag


def _normalize_semantic_value(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    if isinstance(value, list):
        normalized = [_normalize_semantic_value(item) for item in value]
        if all(isinstance(item, (str, int, float, bool, type(None))) for item in normalized):
            return sorted(normalized, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
        return normalized
    if isinstance(value, dict):
        return {key: _normalize_semantic_value(item) for key, item in sorted(value.items())}
    return value


def _semantic_key(requirement: Any) -> str:
    payload = requirement.model_dump(
        exclude={"requirement_id", "modality", "evidence"},
        exclude_none=True,
    )
    normalized = _normalize_semantic_value(payload)
    if isinstance(requirement, SkillRequirement):
        normalized["items"] = sorted(
            normalized["items"],
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_business_rules(result: JDExtractionResult) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    if result.job_title is None:
        flags.append(_scoped_flag(result.document_id, "missing_job_title", ""))
    if not result.company_facts:
        flags.append(_scoped_flag(result.document_id, "missing_company_fact", ""))
    if not any(fact.kind == "company_name" for fact in result.company_facts):
        flags.append(_scoped_flag(result.document_id, "missing_company_name", ""))
    for fact in result.company_facts:
        if fact.kind == "other":
            flags.append(
                _scoped_flag(
                    result.document_id,
                    "company_fact_other_requires_review",
                    fact.evidence.quote,
                    item_id=fact.fact_id,
                )
            )
    for requirement in [*result.responsibilities, *result.requirements]:
        if requirement.modality == "unknown":
            flags.append(_flag(result, requirement.requirement_id, "unknown_modality", requirement.evidence.quote))
        if isinstance(requirement, SkillRequirement):
            keys = [(item.name.casefold(), item.item_type) for item in requirement.items]
            if len(keys) != len(set(keys)):
                flags.append(
                    _flag(result, requirement.requirement_id, "duplicate_skill_in_requirement", requirement.evidence.quote)
                )
            for item in requirement.items:
                if item.item_type == "other":
                    flags.append(
                        _scoped_flag(
                            result.document_id,
                            "skill_item_other_requires_review",
                            requirement.evidence.quote,
                            requirement_id=requirement.requirement_id,
                            item_id=item.name,
                        )
                    )
        if isinstance(requirement, EducationRequirement) and not any(
            [requirement.minimum_degree, requirement.majors, requirement.school_constraints,
             requirement.admission_type, requirement.graduation_year, requirement.student_cohort]
        ):
            flags.append(_flag(result, requirement.requirement_id, "empty_structured_constraint", requirement.evidence.quote))
        if isinstance(requirement, ExperienceRequirement) and not any(
            [requirement.minimum_years is not None, requirement.maximum_years is not None,
             requirement.domain, requirement.role, requirement.duration_text, requirement.experience_unlimited]
        ):
            flags.append(_flag(result, requirement.requirement_id, "empty_structured_constraint", requirement.evidence.quote))
    requirements = [*result.responsibilities, *result.requirements]
    seen_semantics: dict[str, dict[str, Any]] = {}
    for index, requirement in enumerate(requirements):
        semantic_key = _semantic_key(requirement)
        seen_modalities = seen_semantics.setdefault(semantic_key, {})
        earlier_duplicate = seen_modalities.get(requirement.modality)
        if earlier_duplicate is not None:
            flags.append(
                _flag(
                    result,
                    requirement.requirement_id,
                    "duplicate_requirement_semantics",
                    requirement.evidence.quote,
                    related_requirement_id=earlier_duplicate.requirement_id,
                )
            )
            continue
        if seen_modalities:
            earlier_conflict = next(iter(seen_modalities.values()))
            flags.append(
                _flag(
                    result,
                    requirement.requirement_id,
                    "conflicting_requirement_modality",
                    requirement.evidence.quote,
                    related_requirement_id=earlier_conflict.requirement_id,
                    related_modality=earlier_conflict.modality,
                    modality=requirement.modality,
                )
            )
        seen_modalities[requirement.modality] = requirement
        quote = requirement.evidence.quote
        for earlier in requirements[:index]:
            if requirement.kind != earlier.kind:
                continue
            if requirement.evidence.source_id != earlier.evidence.source_id:
                continue
            earlier_quote = earlier.evidence.quote
            overlapping_skill_items = (
                isinstance(requirement, SkillRequirement)
                and isinstance(earlier, SkillRequirement)
                and bool(
                    {(item.name.casefold(), item.item_type) for item in requirement.items}
                    & {(item.name.casefold(), item.item_type) for item in earlier.items}
                )
            )
            if (
                overlapping_skill_items
                and quote != earlier_quote
                and (quote in earlier_quote or earlier_quote in quote)
            ):
                flags.append(
                    _flag(
                        result,
                        requirement.requirement_id,
                        "overlapping_requirement_evidence",
                        quote,
                        related_requirement_id=earlier.requirement_id,
                    )
                )
                break
    return flags


def validate_normalized_rules(result: JDNormalizedResult) -> list[dict[str, Any]]:
    classification = result.job_classification
    if classification is None or classification.classification_status in {
        "resolved",
        "manually_confirmed",
    }:
        return []
    return [
        _scoped_flag(
            result.document_id,
            "job_classification_not_resolved",
            "岗位分类未达到发布状态",
            classification_status=classification.classification_status,
            position_code=classification.position_code,
            review_reason_codes=classification.review_reason_codes,
        )
    ]
