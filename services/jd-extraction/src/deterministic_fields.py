from __future__ import annotations

from copy import deepcopy
import re
import unicodedata
from typing import Any

from .salary_parser import SALARY_PATTERN
from .normalizer import lookup_skill_mapping, skill_mapping_candidates


EMPLOYMENT_KIND_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("allowance", re.compile(r"补贴|补助|津贴|补偿|补发")),
    ("social_security", re.compile(r"社保|社会保险|公积金|医疗保险|养老保险|失业保险|工伤保险|生育保险|(?:五|六|七|八)险(?:一金|二金)?")),
    ("equity", re.compile(r"股权|期权|股票")),
    ("bonus", re.compile(r"奖金|年终奖|绩效奖|项目奖|全勤奖")),
    ("leave", re.compile(r"年假|休假|带薪假|生日假|婚假|产假|陪产假")),
    ("health_check", re.compile(r"体检")),
    ("training", re.compile(r"培训|导师制|课程学习")),
    ("team_activity", re.compile(r"团建|员工旅游|团队活动")),
    ("accommodation", re.compile(r"住宿|宿舍|住房")),
    ("meal", re.compile(r"工作餐|免费餐|早晚餐|午餐|晚餐|食堂|包吃|下午茶|零食")),
)

REQUIRED_SECTION_HEADINGS = {
    "requirements", "qualifications", "what you bring", "what we value", "who you are",
    "candidate profile", "source.requirement", "任职要求", "职位要求", "岗位要求",
    "能力要求", "任职资格", "我们希望", "我们重视",
}
NON_REQUIRED_SECTION_HEADINGS = {
    "preferred qualifications", "nice to have", "bonus points", "加分项", "优先条件",
    "responsibilities", "what you will do", "source.description", "岗位职责", "职位职责",
    "工作职责",
}
STRUCTURED_REQUIRED_REQUIREMENT = re.compile(
    r"^(?:(?:学历|学历要求|经验|经验要求)\s*[:：]|经验不限$)"
)


def _normalization_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _section_heading(value: str) -> str | None:
    normalized = _normalization_key(value).strip(" :：[]【】#-*•")
    if normalized in REQUIRED_SECTION_HEADINGS:
        return "required"
    if normalized in NON_REQUIRED_SECTION_HEADINGS:
        return "other"
    return None


def infer_employment_kind(value: str) -> str | None:
    """Infer a kind only when a general lexical rule identifies one category."""
    matches = [kind for kind, pattern in EMPLOYMENT_KIND_PATTERNS if pattern.search(value)]
    return matches[0] if len(matches) == 1 else None


def _structured_header(source_blocks: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
    if len(source_blocks) < 2:
        return None
    title_block = source_blocks[0]
    title = str(title_block.get("text", "")).strip()
    salary = str(source_blocks[1].get("text", "")).strip()
    if not title or len(title) > 80 or SALARY_PATTERN.search(title) or not SALARY_PATTERN.search(salary):
        return None
    if any(mark in title for mark in ("。", "；", ";")):
        return None
    if any(title.startswith(prefix) for prefix in (
        "岗位职责", "职位职责", "工作职责", "任职要求", "职位要求", "岗位要求",
        "公司介绍", "企业介绍", "工作地点", "薪资", "职位描述", "岗位描述",
    )):
        return None
    company_block = source_blocks[2] if len(source_blocks) >= 3 else None
    if company_block is not None:
        company = str(company_block.get("text", "")).strip()
        if (
            not company
            or len(company) > 60
            or any(cue in company for cue in ("...", "…", "名字", "保密", "某公司"))
        ):
            company_block = None
    return title_block, company_block


def canonicalize_authoritative_fields(
    payload: dict[str, Any],
    normalization_map: dict[str, Any],
    source_blocks: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Apply only values determined by formal config or structured source evidence."""
    canonicalized = deepcopy(payload)
    corrections: list[dict[str, Any]] = []

    section_by_source_id: dict[str, str | None] = {}
    active_section: str | None = None
    for block in source_blocks:
        text = str(block.get("text", "")).strip()
        heading = _section_heading(text) if len(text) <= 80 else None
        if heading is not None:
            active_section = heading
        section_by_source_id[str(block.get("source_id"))] = active_section

    requirements = canonicalized.get("requirements")
    for requirement_index, requirement in enumerate(requirements if isinstance(requirements, list) else []):
        if not isinstance(requirement, dict):
            continue
        evidence = requirement.get("evidence")
        source_id = evidence.get("source_id") if isinstance(evidence, dict) else None
        if requirement.get("modality") == "unknown" and section_by_source_id.get(str(source_id)) == "required":
            requirement["modality"] = "required"
            corrections.append({
                "path": f"requirements[{requirement_index}].modality",
                "from": "unknown",
                "to": "required",
                "authority": "requirement_section_context",
            })
        elif (
            requirement.get("modality") == "unknown"
            and isinstance(evidence, dict)
            and isinstance(evidence.get("quote"), str)
            and STRUCTURED_REQUIRED_REQUIREMENT.match(evidence["quote"].strip())
        ):
            requirement["modality"] = "required"
            corrections.append({
                "path": f"requirements[{requirement_index}].modality",
                "from": "unknown",
                "to": "required",
                "authority": "structured_requirement_label",
            })
        if requirement.get("kind") != "skill":
            continue
        for item_index, item in enumerate(requirement.get("items", [])):
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                continue
            current = item.get("item_type")
            mapping = lookup_skill_mapping(normalization_map, item["name"], current) if isinstance(current, str) else None
            if mapping is not None:
                continue
            candidates = skill_mapping_candidates(normalization_map, item["name"])
            expected = candidates[0].get("category_code") if len(candidates) == 1 else None
            if isinstance(expected, str) and current != expected:
                item["item_type"] = expected
                corrections.append({
                    "path": f"requirements[{requirement_index}].items[{item_index}].item_type",
                    "from": current,
                    "to": expected,
                    "authority": "normalization_map",
                })

    employment_facts = canonicalized.get("employment_facts")
    for fact_index, fact in enumerate(employment_facts if isinstance(employment_facts, list) else []):
        if not isinstance(fact, dict) or not isinstance(fact.get("value"), str):
            continue
        expected = infer_employment_kind(fact["value"])
        current = fact.get("kind")
        if expected is not None and current != expected:
            fact["kind"] = expected
            corrections.append({
                "path": f"employment_facts[{fact_index}].kind",
                "from": current,
                "to": expected,
                "authority": "employment_kind_ontology",
            })

    header = _structured_header(source_blocks)
    if header is not None:
        title_block, company_block = header
        expected_title = {
            "value": title_block["text"],
            "evidence": {"source_id": title_block["source_id"], "quote": title_block["text"]},
        }
        current_title = canonicalized.get("job_title")
        current_evidence = current_title.get("evidence") if isinstance(current_title, dict) else None
        title_matches = (
            isinstance(current_title, dict)
            and current_title.get("value") == expected_title["value"]
            and isinstance(current_evidence, dict)
            and current_evidence.get("source_id") == expected_title["evidence"]["source_id"]
            and current_evidence.get("quote") == expected_title["evidence"]["quote"]
        )
        if not title_matches:
            corrections.append({
                "path": "job_title",
                "from": current_title,
                "to": expected_title,
                "authority": "structured_source_header",
            })
            canonicalized["job_title"] = expected_title

        company_facts = canonicalized.get("company_facts")
        if company_block is not None and isinstance(company_facts, list) and not any(
            isinstance(fact, dict) and fact.get("kind") == "company_name"
            for fact in company_facts
        ):
            company_fact = {
                "kind": "company_name",
                "value": company_block["text"],
                "evidence": {"source_id": company_block["source_id"], "quote": company_block["text"]},
            }
            company_facts.append(company_fact)
            corrections.append({
                "path": f"company_facts[{len(company_facts) - 1}]",
                "from": None,
                "to": company_fact,
                "authority": "structured_source_header",
            })

    return canonicalized, corrections


def populate_deterministic_fields(payload: dict, document_id: str) -> dict:
    payload["document_id"] = document_id
    requirement_index = 0
    for collection_name in ("responsibilities", "requirements"):
        collection = payload.get(collection_name)
        if not isinstance(collection, list):
            continue
        for requirement in collection:
            if not isinstance(requirement, dict):
                continue
            requirement_index += 1
            requirement["requirement_id"] = f"req_{requirement_index:03d}"
            evidence = requirement.get("evidence")
            if isinstance(evidence, dict):
                evidence.update({"start": None, "end": None, "alignment": "unresolved", "occurrence_index": None})
    for prefix, collection_name in (("company", "company_facts"), ("employment", "employment_facts")):
        collection = payload.get(collection_name)
        if not isinstance(collection, list):
            continue
        for index, fact in enumerate(collection, start=1):
            if not isinstance(fact, dict):
                continue
            fact["fact_id"] = f"{prefix}_{index:03d}"
            evidence = fact.get("evidence")
            if isinstance(evidence, dict):
                evidence.update({"start": None, "end": None, "alignment": "unresolved", "occurrence_index": None})
    job_title = payload.get("job_title")
    if isinstance(job_title, dict) and isinstance(job_title.get("evidence"), dict):
        job_title["evidence"].update(
            {"start": None, "end": None, "alignment": "unresolved", "occurrence_index": None}
        )
    return payload
