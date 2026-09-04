from __future__ import annotations

import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
import unicodedata


PROJECT_ROOT = Path(__file__).resolve().parents[1]
JOBPULSE_ROOT = PROJECT_ROOT.parents[1]
MAIN_ROOT = JOBPULSE_ROOT / "apps" / "api"
CV_ROOT = JOBPULSE_ROOT / "services" / "cv-extraction"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.deepseek_client import DeepSeekClient  # noqa: E402
from src.normalizer import load_normalization_map  # noqa: E402
from src.skill_taxonomy import load_skill_taxonomy_snapshot  # noqa: E402


ALLOWED_LEGACY_CATEGORIES = {
    "programming_language",
    "framework",
    "library",
    "database",
    "tool",
    "platform",
    "methodology",
    "domain_knowledge",
    "other",
}
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
URL_PATTERN = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
ID_NUMBER_PATTERN = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _candidate_id(source_name: str, item_type: str) -> str:
    return f"{_text_key(source_name)}:{item_type}"


def _text_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _redact_quote(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = EMAIL_PATTERN.sub("[EMAIL]", value)
    text = PHONE_PATTERN.sub("[PHONE]", text)
    text = ID_NUMBER_PATTERN.sub("[ID]", text)
    text = URL_PATTERN.sub("[URL]", text)
    return text[:320]


def _merge_candidate(
    grouped: dict[tuple[str, str], dict[str, Any]],
    *,
    source_name: str,
    item_type: str,
    document_id: str,
    quote: Any,
    source: str,
) -> None:
    key = (source_name, item_type)
    entry = grouped.setdefault(
        key,
        {
            "source_name": source_name,
            "item_type": item_type,
            "document_ids": set(),
            "evidence_samples": [],
            "sources": set(),
        },
    )
    entry["document_ids"].add(document_id)
    entry["sources"].add(source)
    redacted = _redact_quote(quote)
    if redacted and redacted not in entry["evidence_samples"]:
        entry["evidence_samples"].append(redacted)


def _jd_candidates(
    pool_path: Path,
    normalization_map: dict[str, Any],
) -> dict[tuple[str, str], dict[str, Any]]:
    pool = json.loads(pool_path.read_text(encoding="utf-8"))
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in pool["candidates"].values():
        source_name = entry["source_name"]
        item_type = entry["item_type"]
        exact_key = " ".join(unicodedata.normalize("NFKC", source_name).split())
        if (exact_key, item_type) in normalization_map[
            "_skills_by_exact_typed_key"
        ]:
            continue
        for document_id in entry.get("document_ids", []):
            samples = entry.get("evidence_samples", [])
            matching = [
                sample
                for sample in samples
                if sample.get("jd_id") == document_id
            ]
            _merge_candidate(
                grouped,
                source_name=source_name,
                item_type=item_type,
                document_id=str(document_id),
                quote=matching[0].get("quote") if matching else None,
                source="jd",
            )
    return grouped


def _cv_skill_items(annotation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    items = {
        item["item_id"]: item
        for item in annotation.get("skills", [])
        if isinstance(item, dict) and isinstance(item.get("item_id"), str)
    }
    for collection in ("work_experience", "project_experience"):
        for entry in annotation.get(collection, []):
            for item in entry.get("tech_stack", []):
                if isinstance(item, dict) and isinstance(item.get("item_id"), str):
                    items[item["item_id"]] = item
    return items


def _add_cv_candidates(
    grouped: dict[tuple[str, str], dict[str, Any]],
    cv_runs_root: Path,
) -> None:
    latest: dict[str, tuple[str, dict[str, Any], dict[str, Any]]] = {}
    for final_dir in sorted(cv_runs_root.glob("*/final")):
        annotations = {
            item["document_id"]: item
            for item in _read_jsonl(final_dir / "annotations.jsonl")
        }
        normalized = {
            item["document_id"]: item
            for item in json.loads(
                (final_dir / "normalized_annotations.json").read_text(encoding="utf-8")
            )
        }
        for document_id in annotations.keys() & normalized.keys():
            latest[document_id] = (
                final_dir.parent.name,
                annotations[document_id],
                normalized[document_id],
            )
    for document_id, (_, annotation, normalized) in latest.items():
        by_id = _cv_skill_items(annotation)
        for skill in normalized.get("normalized_skills", []):
            if skill.get("identity_resolution_status") != "unresolved":
                continue
            item = by_id.get(skill.get("source_item_id"))
            if item is None:
                raise ValueError(
                    f"CV unresolved item is missing from annotation: {document_id} "
                    f"{skill.get('source_item_id')}"
                )
            _merge_candidate(
                grouped,
                source_name=item["name"],
                item_type=item["item_type"],
                document_id=document_id,
                quote=(item.get("evidence") or {}).get("quote"),
                source="cv",
            )


def build_candidates(
    pool_path: Path,
    normalization_path: Path,
    cv_runs_root: Path,
) -> list[dict[str, Any]]:
    normalization_map = load_normalization_map(str(normalization_path))
    grouped = _jd_candidates(pool_path, normalization_map)
    _add_cv_candidates(grouped, cv_runs_root)
    candidates = []
    for entry in grouped.values():
        candidates.append(
            {
                "candidate_id": _candidate_id(
                    entry["source_name"], entry["item_type"]
                ),
                "source_name": entry["source_name"],
                "item_type": entry["item_type"],
                "document_count": len(entry["document_ids"]),
                "sources": sorted(entry["sources"]),
                "evidence_samples": entry["evidence_samples"][:2],
            }
        )
    return sorted(
        candidates,
        key=lambda item: (_text_key(item["source_name"]), item["item_type"]),
    )


def _load_api_key(env_path: Path) -> None:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    base_url = os.environ.get("DEEPSEEK_BASE_URL")
    if api_key and base_url:
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if not separator or not value.strip():
            continue
        name = name.strip()
        value = value.strip()
        if name == "DEEPSEEK_API_KEY" and not api_key:
            api_key = value
            os.environ["DEEPSEEK_API_KEY"] = value
        elif name == "DEEPSEEK_BASE_URL" and not base_url:
            base_url = value
            os.environ["DEEPSEEK_BASE_URL"] = value
    if not api_key:
        raise ValueError(f"DEEPSEEK_API_KEY is missing from {env_path}")


def _compact_catalog(
    taxonomy: dict[str, Any], normalization_map: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    del normalization_map
    skills = [
        {
            "skill_id": skill_id,
            "canonical_name": entry["canonical_name"],
        }
        for skill_id, entry in sorted(taxonomy["skills"].items())
    ]
    return skills, []


SYSTEM_PROMPT = """你是技能本体的最终语义审查员。对每个候选必须给出且只给出一个最终决定。

输出紧凑 JSON object：
{"decisions":[{"id":str,"action":"alias_existing"|"create_new"|"reject","target_skill_id":str|null,"skill_id":str|null,"canonical_name":str|null,"legacy_category_code":str|null,"legacy_subcategory_code":str|null,"concept_class":str|null,"technology_kind":str|null,"primary_domain":str|null,"secondary_domains":list,"reason":str}]}

决定规则：
1. alias_existing：候选与 existing_skills 中某个 canonical identity 完全相同，target_skill_id 必须来自 existing_skills。上下位、相似或共同出现不算相同。
2. create_new：候选是边界稳定、可跨数据复用的技能实体。出现次数只影响审查优先级；即使仅出现一次，只要名称与 Evidence 明确，也允许新建。
3. reject：候选不是技能、只是任务动作/能力描述/残片/参数，或 Evidence 无法消歧。不要为了清零而猜测。
4. create_new 必须填写 skill_id、canonical_name、legacy_category_code、concept_class；没有可靠旧 subcategory 时 legacy_subcategory_code 为 null。skill_id 使用大写 ASCII、数字、下划线，并采用 LANG/FRAMEWORK/LIBRARY/DATABASE/TOOL/PLATFORM/METHOD/KNOWLEDGE/OTHER 前缀。
5. concept_class、technology_kind、primary_domain、secondary_domains 是直接的新标准语义判断，不得从 legacy category 推导。technology 必须填写 technology_kind，其他 concept_class 的 technology_kind 必须为 null；domain 不适用时 primary_domain=null 且 secondary_domains=[]。
6. concept_class 可选 knowledge、practice、technology、transversal_skill。
7. technology_kind 可选 algorithm_model、database_storage、framework、hardware_system、language、library_sdk、middleware_runtime、platform_service、protocol_standard、tool。
8. domain 可选 ai_intelligent_systems、blockchain_web3、cloud_distributed、computing_hardware、cybersecurity_privacy、data_engineering、digital_governance、embedded_iot_edge、hci_graphics_xr、network_communications、quantum_computing、robotics_autonomy、software_engineering。没有可靠 domain 时不生成 domain，并设 domain_decision=not_applicable；禁止生成 other 或伪领域。
9. legacy_category_code 仅用于兼容抽取 item_type，可选 programming_language、framework、library、database、tool、platform、methodology、domain_knowledge、other；它不是新分类权威。
10. 专有技术、模型、协议、标准、工具、框架、语言、理论和稳定方法可以是技能；“熟练使用”“负责开发”“优化效果”等句子片段不是技能。
11. 每个 candidate_id 必须以 id 出现一次；reason 控制在 30 个汉字内，说明实体边界或拒绝原因。不要输出 Markdown 或额外文字。"""

IDENTITY_PROMPT = """你是技能 canonical identity 的最终审查员。输出紧凑 JSON：
{"decisions":[{"id":str,"action":"alias_existing"|"create_new"|"reject","target_skill_id":str|null,"skill_id":str|null,"canonical_name":str|null,"legacy_category_code":str|null,"legacy_subcategory_code":str|null,"reason":str}]}

规则：
1. alias_existing 仅用于与 existing_skills 完全相同的实体，target_skill_id 必须真实存在；上下位或相似不算相同。
2. create_new 用于边界稳定、可跨数据复用的技能；出现次数只影响优先级，单次明确 Evidence 也允许创建。
3. reject 用于任务动作、能力句、参数、残片、过窄项目描述或无法消歧的词；不得为清零而猜。
4. create_new 必须填写 skill_id、canonical_name、legacy_category_code；legacy_subcategory_code 无可靠值时为 null。
5. skill_id 仅用大写 ASCII、数字和下划线，前缀限 LANG/FRAMEWORK/LIBRARY/DATABASE/TOOL/PLATFORM/METHOD/KNOWLEDGE/OTHER。
6. legacy_category_code 可选 programming_language、framework、library、database、tool、platform、methodology、domain_knowledge、other，仅为抽取兼容类型。
7. 每个 id 恰好一次，reason 不超过 24 个汉字。只输出 JSON。"""

CLASSIFICATION_PROMPT = """你是技能多维 taxonomy 的最终分类员。输入都已确认是新的 canonical identity。
输出紧凑 JSON：
{"classifications":[{"skill_id":str,"concept_class":str,"technology_kind":str|null,"primary_domain":str|null,"secondary_domains":list}]}

规则：
1. concept_class 必须是 knowledge、practice、technology、transversal_skill 之一。
2. technology 必须填写一个 technology_kind；其他 concept_class 必须为 null。technology_kind 可选 algorithm_model、database_storage、framework、hardware_system、language、library_sdk、middleware_runtime、platform_service、protocol_standard、tool。
3. domain 可选 ai_intelligent_systems、blockchain_web3、cloud_distributed、computing_hardware、cybersecurity_privacy、data_engineering、digital_governance、embedded_iot_edge、hci_graphics_xr、network_communications、quantum_computing、robotics_autonomy、software_engineering。
4. 无可靠 domain 时 primary_domain=null 且 secondary_domains=[]；禁止生成 other 或伪领域。secondary_domains 不得重复 primary_domain。
5. 分类必须按 canonical identity 本身判断，不能从 legacy category 推导。每个 skill_id 恰好一次。只输出 JSON。"""


def _request_batch(
    batch: list[dict[str, Any]],
    *,
    model: str,
    existing_skills: list[dict[str, Any]],
    existing_aliases: list[dict[str, str]],
    nodes: list[dict[str, Any]],
    correction: str | None = None,
) -> list[dict[str, Any]]:
    del existing_aliases
    payload = {
            "taxonomy_nodes": nodes,
            "existing_skills": existing_skills,
            "candidates": batch,
        }
    if correction is not None:
        payload["previous_validation_error"] = correction
        payload["correction_requirement"] = (
            "修正该错误并重新输出本批全部候选；不存在于 existing_skills 的 "
            "skill_id 禁止作为 alias_existing target。"
        )
    user = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    response = DeepSeekClient(model=model, timeout=180).extract(
        SYSTEM_PROMPT, user
    ).data
    decisions = response.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError("DeepSeek final review response has no decisions list")
    return decisions


def _validate_classifications(
    relations: Any,
    active_nodes: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    if not isinstance(relations, list):
        raise ValueError("create_new classifications must be a list")
    normalized = []
    seen: set[tuple[str, str]] = set()
    for relation in relations:
        if not isinstance(relation, dict):
            raise ValueError("classification must be an object")
        facet = relation.get("facet")
        code = relation.get("code")
        primary = relation.get("is_primary")
        if (facet, code) not in active_nodes or not isinstance(primary, bool):
            raise ValueError(f"invalid classification: {relation!r}")
        if (facet, code) in seen:
            raise ValueError(f"duplicate classification: {relation!r}")
        seen.add((facet, code))
        normalized.append(
            {"facet": facet, "code": code, "is_primary": primary}
        )
    concepts = [item for item in normalized if item["facet"] == "concept_class"]
    kinds = [item for item in normalized if item["facet"] == "technology_kind"]
    domains = [item for item in normalized if item["facet"] == "domain"]
    if len(concepts) != 1 or not concepts[0]["is_primary"]:
        raise ValueError("create_new must have one primary concept_class")
    if (concepts[0]["code"] == "technology") != (len(kinds) == 1):
        raise ValueError("technology_kind conflicts with concept_class")
    if kinds and not kinds[0]["is_primary"]:
        raise ValueError("technology_kind must be primary")
    if sum(item["is_primary"] for item in domains) > 1:
        raise ValueError("domain has multiple primary values")
    return normalized


def validate_decisions(
    candidates: list[dict[str, Any]],
    raw_decisions: list[dict[str, Any]],
    taxonomy: dict[str, Any],
) -> list[dict[str, Any]]:
    candidate_by_id = {item["candidate_id"]: item for item in candidates}
    existing_ids = set(taxonomy["skills"])
    active_nodes = {
        (node["facet"], node["code"])
        for node in taxonomy["nodes"]
        if node["status"] == "active"
    }
    validated: dict[str, dict[str, Any]] = {}
    for raw in raw_decisions:
        if not isinstance(raw, dict):
            raise ValueError("decision must be an object")
        candidate_id = raw.get("id")
        if candidate_id not in candidate_by_id or candidate_id in validated:
            raise ValueError(f"unknown or duplicate candidate_id: {candidate_id!r}")
        action = raw.get("action")
        reason = raw.get("reason")
        if action not in {"alias_existing", "create_new", "reject"}:
            raise ValueError(f"invalid action for {candidate_id}: {action!r}")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"decision reason is empty for {candidate_id}")
        decision = {
            **candidate_by_id[candidate_id],
            "action": action,
            "reason": reason.strip(),
        }
        if action == "alias_existing":
            target = raw.get("target_skill_id")
            if target not in existing_ids:
                raise ValueError(
                    f"alias target is not in the reviewed catalog: {target!r}"
                )
            decision["target_skill_id"] = target
        elif action == "create_new":
            mapping = {
                "skill_id": raw.get("skill_id"),
                "canonical_name": raw.get("canonical_name"),
                "legacy_category_code": raw.get("legacy_category_code"),
                "legacy_subcategory_code": raw.get("legacy_subcategory_code"),
            }
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*", str(mapping["skill_id"])):
                raise ValueError(f"invalid skill_id for {candidate_id}")
            if (
                not isinstance(mapping["canonical_name"], str)
                or not mapping["canonical_name"].strip()
                or mapping["legacy_category_code"] not in ALLOWED_LEGACY_CATEGORIES
                or (
                    mapping["legacy_subcategory_code"] is not None
                    and (
                        not isinstance(mapping["legacy_subcategory_code"], str)
                        or not mapping["legacy_subcategory_code"].strip()
                    )
                )
            ):
                raise ValueError(f"invalid create_new mapping for {candidate_id}")
            concept_class = raw.get("concept_class")
            technology_kind = raw.get("technology_kind")
            primary_domain = raw.get("primary_domain")
            secondary_domains = raw.get("secondary_domains")
            if not isinstance(secondary_domains, list):
                raise ValueError(f"secondary_domains must be a list for {candidate_id}")
            relations: list[dict[str, Any]] = [
                {
                    "facet": "concept_class",
                    "code": concept_class,
                    "is_primary": True,
                }
            ]
            if technology_kind is not None:
                relations.append(
                    {
                        "facet": "technology_kind",
                        "code": technology_kind,
                        "is_primary": True,
                    }
                )
            if primary_domain is not None:
                relations.append(
                    {
                        "facet": "domain",
                        "code": primary_domain,
                        "is_primary": True,
                    }
                )
            relations.extend(
                {
                    "facet": "domain",
                    "code": domain,
                    "is_primary": False,
                }
                for domain in secondary_domains
            )
            relations = _validate_classifications(relations, active_nodes)
            has_domain = any(item["facet"] == "domain" for item in relations)
            expected_decision = "classified" if has_domain else "not_applicable"
            decision["mapping"] = {
                key: str(value).strip() if value is not None else None
                for key, value in mapping.items()
            }
            decision["classifications"] = relations
            decision["domain_decision"] = expected_decision
        validated[candidate_id] = decision
    missing = sorted(set(candidate_by_id) - set(validated))
    if missing:
        raise ValueError(f"DeepSeek omitted {len(missing)} candidates: {missing[:10]}")
    return [validated[item["candidate_id"]] for item in candidates]


def _request_validated_batch(
    batch: list[dict[str, Any]],
    *,
    model: str,
    existing_skills: list[dict[str, Any]],
    existing_aliases: list[dict[str, str]],
    taxonomy: dict[str, Any],
) -> list[dict[str, Any]]:
    errors: list[str] = []
    for _ in range(1 if len(batch) > 10 else 3):
        raw = _request_batch(
            batch,
            model=model,
            existing_skills=existing_skills,
            existing_aliases=existing_aliases,
            nodes=taxonomy["nodes"],
            correction=errors[-1] if errors else None,
        )
        try:
            return validate_decisions(batch, raw, taxonomy)
        except ValueError as error:
            errors.append(str(error))
    raise ValueError("DeepSeek batch failed semantic validation: " + " | ".join(errors))


def _request_identity_batch(
    batch: list[dict[str, Any]],
    *,
    model: str,
    existing_skills: list[dict[str, Any]],
    existing_ids: set[str],
) -> list[dict[str, Any]]:
    correction: str | None = None
    candidate_by_id = {item["candidate_id"]: item for item in batch}
    for _ in range(1 if len(batch) > 10 else 3):
        payload: dict[str, Any] = {
            "existing_skills": existing_skills,
            "candidates": batch,
        }
        if correction is not None:
            payload["previous_validation_error"] = correction
            payload["correction_requirement"] = (
                "修正错误并重新输出本批全部候选；不得引用不存在的 skill_id。"
            )
        raw = DeepSeekClient(model=model, timeout=180).extract(
            IDENTITY_PROMPT,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        ).data.get("decisions")
        try:
            if not isinstance(raw, list):
                raise ValueError("identity response has no decisions list")
            result: dict[str, dict[str, Any]] = {}
            for item in raw:
                if not isinstance(item, dict):
                    raise ValueError("identity decision must be an object")
                candidate_id = item.get("id")
                if candidate_id not in candidate_by_id or candidate_id in result:
                    raise ValueError(f"unknown or duplicate id: {candidate_id!r}")
                action = item.get("action")
                reason = item.get("reason")
                if action not in {"alias_existing", "create_new", "reject"}:
                    raise ValueError(f"invalid action: {action!r}")
                if not isinstance(reason, str) or not reason.strip():
                    raise ValueError(f"empty reason: {candidate_id}")
                decision = {
                    **candidate_by_id[candidate_id],
                    "action": action,
                    "reason": reason.strip(),
                }
                if action == "alias_existing":
                    target = item.get("target_skill_id")
                    if target not in existing_ids:
                        raise ValueError(f"unknown alias target: {target!r}")
                    decision["target_skill_id"] = target
                elif action == "create_new":
                    mapping = {
                        "skill_id": item.get("skill_id"),
                        "canonical_name": item.get("canonical_name"),
                        "legacy_category_code": item.get(
                            "legacy_category_code"
                        ),
                        "legacy_subcategory_code": item.get(
                            "legacy_subcategory_code"
                        ),
                    }
                    if not re.fullmatch(
                        r"[A-Z][A-Z0-9_]*", str(mapping["skill_id"])
                    ):
                        raise ValueError(f"invalid skill_id: {candidate_id}")
                    if (
                        not isinstance(mapping["canonical_name"], str)
                        or not mapping["canonical_name"].strip()
                        or mapping["legacy_category_code"]
                        not in ALLOWED_LEGACY_CATEGORIES
                        or (
                            mapping["legacy_subcategory_code"] is not None
                            and not isinstance(
                                mapping["legacy_subcategory_code"], str
                            )
                        )
                    ):
                        raise ValueError(f"invalid mapping: {candidate_id}")
                    decision["mapping"] = mapping
                result[candidate_id] = decision
            missing = set(candidate_by_id) - set(result)
            if missing:
                raise ValueError(f"omitted ids: {sorted(missing)[:5]}")
            return [result[item["candidate_id"]] for item in batch]
        except ValueError as error:
            correction = str(error)
    if len(batch) > 1:
        midpoint = len(batch) // 2
        return [
            *_request_identity_batch(
                batch[:midpoint],
                model=model,
                existing_skills=existing_skills,
                existing_ids=existing_ids,
            ),
            *_request_identity_batch(
                batch[midpoint:],
                model=model,
                existing_skills=existing_skills,
                existing_ids=existing_ids,
            ),
        ]
    raise ValueError(f"identity candidate failed validation: {correction}")


def _consolidate_new_identities(
    decisions: list[dict[str, Any]], taxonomy: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    existing_by_name = {
        _text_key(entry["canonical_name"]): skill_id
        for skill_id, entry in taxonomy["skills"].items()
    }
    new_by_name: dict[str, dict[str, Any]] = {}
    id_to_name = {
        skill_id: _text_key(entry["canonical_name"])
        for skill_id, entry in taxonomy["skills"].items()
    }
    for decision in decisions:
        if decision["action"] != "create_new":
            continue
        mapping = decision["mapping"]
        canonical_key = _text_key(mapping["canonical_name"])
        existing_target = existing_by_name.get(canonical_key)
        if existing_target is not None:
            decision["action"] = "alias_existing"
            decision["target_skill_id"] = existing_target
            decision.pop("mapping")
            continue
        first = new_by_name.get(canonical_key)
        if first is not None:
            decision["action"] = "alias_existing"
            decision["target_skill_id"] = first["mapping"]["skill_id"]
            decision.pop("mapping")
            continue
        skill_id = mapping["skill_id"]
        previous_name = id_to_name.setdefault(skill_id, canonical_key)
        if previous_name != canonical_key:
            raise ValueError(
                f"new skill_id collision: {skill_id} maps to multiple names"
            )
        new_by_name[canonical_key] = decision
    identities = [
        {
            "skill_id": decision["mapping"]["skill_id"],
            "canonical_name": decision["mapping"]["canonical_name"],
            "evidence_samples": decision["evidence_samples"],
        }
        for decision in new_by_name.values()
    ]
    return decisions, identities


def _request_classification_batch(
    batch: list[dict[str, Any]],
    *,
    model: str,
    taxonomy: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    active_nodes = {
        (node["facet"], node["code"])
        for node in taxonomy["nodes"]
        if node["status"] == "active"
    }
    expected = {item["skill_id"] for item in batch}
    correction: str | None = None
    for _ in range(1 if len(batch) > 10 else 3):
        payload: dict[str, Any] = {"skills": batch}
        if correction is not None:
            payload["previous_validation_error"] = correction
            payload["correction_requirement"] = (
                "修正错误并重新输出本批全部 skill_id。"
            )
        raw = DeepSeekClient(model=model, timeout=180).extract(
            CLASSIFICATION_PROMPT,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        ).data.get("classifications")
        try:
            if not isinstance(raw, list):
                raise ValueError("classification response has no list")
            result: dict[str, dict[str, Any]] = {}
            for item in raw:
                if not isinstance(item, dict):
                    raise ValueError("classification result must be an object")
                skill_id = item.get("skill_id")
                if skill_id not in expected or skill_id in result:
                    raise ValueError(f"unknown or duplicate skill_id: {skill_id!r}")
                secondary = item.get("secondary_domains")
                if not isinstance(secondary, list):
                    raise ValueError(f"secondary_domains is not a list: {skill_id}")
                relations: list[dict[str, Any]] = [
                    {
                        "facet": "concept_class",
                        "code": item.get("concept_class"),
                        "is_primary": True,
                    }
                ]
                if item.get("technology_kind") is not None:
                    relations.append(
                        {
                            "facet": "technology_kind",
                            "code": item["technology_kind"],
                            "is_primary": True,
                        }
                    )
                if item.get("primary_domain") is not None:
                    relations.append(
                        {
                            "facet": "domain",
                            "code": item["primary_domain"],
                            "is_primary": True,
                        }
                    )
                relations.extend(
                    {
                        "facet": "domain",
                        "code": domain,
                        "is_primary": False,
                    }
                    for domain in secondary
                )
                relations = _validate_classifications(relations, active_nodes)
                has_domain = any(
                    relation["facet"] == "domain" for relation in relations
                )
                result[skill_id] = {
                    "classifications": relations,
                    "domain_decision": (
                        "classified" if has_domain else "not_applicable"
                    ),
                }
            missing = expected - set(result)
            if missing:
                raise ValueError(f"omitted skill_ids: {sorted(missing)[:5]}")
            return result
        except ValueError as error:
            correction = str(error)
    if len(batch) > 1:
        midpoint = len(batch) // 2
        result = _request_classification_batch(
            batch[:midpoint], model=model, taxonomy=taxonomy
        )
        result.update(
            _request_classification_batch(
                batch[midpoint:], model=model, taxonomy=taxonomy
            )
        )
        return result
    raise ValueError(f"classification skill failed validation: {correction}")


def propose(args: argparse.Namespace) -> None:
    _load_api_key(args.env_file)
    candidates = build_candidates(
        args.candidate_pool, args.normalization, args.cv_runs_root
    )
    taxonomy = load_skill_taxonomy_snapshot(args.catalog)
    normalization_map = load_normalization_map(str(args.normalization))
    existing_skills, _ = _compact_catalog(
        taxonomy, normalization_map
    )
    existing_ids = set(taxonomy["skills"])
    identity_checkpoint = args.output.with_suffix(".identity.tmp.json")
    if identity_checkpoint.is_file():
        identity_decisions = json.loads(
            identity_checkpoint.read_text(encoding="utf-8")
        )["decisions"]
    else:
        identity_decisions = []
    completed_ids = {item["candidate_id"] for item in identity_decisions}
    pending = [
        candidate
        for candidate in candidates
        if candidate["candidate_id"] not in completed_ids
    ]
    pending_batches = [
        pending[index : index + args.batch_size]
        for index in range(0, len(pending), args.batch_size)
    ]
    for index, batch in enumerate(pending_batches, start=1):
        identity_decisions.extend(
            _request_identity_batch(
                batch,
                model=args.model,
                existing_skills=existing_skills,
                existing_ids=existing_ids,
            )
        )
        identity_checkpoint.write_text(
            json.dumps(
                {"decisions": identity_decisions}, ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "identity_batch": index,
                    "identity_batch_count": len(pending_batches),
                    "identity_decisions": len(identity_decisions),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    decisions, new_identities = _consolidate_new_identities(
        identity_decisions, taxonomy
    )
    classification_checkpoint = args.output.with_suffix(
        ".classification.tmp.json"
    )
    if classification_checkpoint.is_file():
        classifications = json.loads(
            classification_checkpoint.read_text(encoding="utf-8")
        )["classifications"]
    else:
        classifications = {}
    unclassified = [
        item for item in new_identities if item["skill_id"] not in classifications
    ]
    classification_batches = [
        unclassified[index : index + args.classification_batch_size]
        for index in range(0, len(unclassified), args.classification_batch_size)
    ]
    for index, batch in enumerate(classification_batches, start=1):
        classifications.update(
            _request_classification_batch(
                batch, model=args.model, taxonomy=taxonomy
            )
        )
        classification_checkpoint.write_text(
            json.dumps(
                {"classifications": classifications},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "classification_batch": index,
                    "classification_batch_count": len(classification_batches),
                    "classified_identities": len(classifications),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    for decision in decisions:
        if decision["action"] != "create_new":
            continue
        classification = classifications.get(decision["mapping"]["skill_id"])
        if classification is None:
            raise ValueError(
                f"new identity has no classification: {decision['mapping']['skill_id']}"
            )
        decision.update(classification)
    payload = {
        "schema": "skill-semantic-final-decisions.v1",
        "model": args.model,
        "candidate_count": len(candidates),
        "data_policy": "candidate_name_type_and_redacted_evidence_only",
        "decisions": decisions,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    identity_checkpoint.unlink()
    classification_checkpoint.unlink(missing_ok=True)
    counts: dict[str, int] = defaultdict(int)
    for decision in decisions:
        counts[decision["action"]] += 1
    print(
        json.dumps(
            {"output": str(args.output), "actions": counts},
            ensure_ascii=False,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Produce final semantic dispositions for every unresolved JD/CV skill."
    )
    parser.add_argument("command", choices=("propose",))
    parser.add_argument(
        "--candidate-pool",
        type=Path,
        default=PROJECT_ROOT / "config" / "normalization_candidate_pool.json",
    )
    parser.add_argument(
        "--normalization",
        type=Path,
        default=PROJECT_ROOT / "config" / "normalization_map.yaml",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=MAIN_ROOT / "config" / "skill_taxonomy_catalog.v1.json",
    )
    parser.add_argument(
        "--cv-runs-root",
        type=Path,
        default=CV_ROOT / "output" / "runs",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=PROJECT_ROOT / ".env",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT
        / "config"
        / "post_review"
        / "skill_semantic_final_decisions_20260729.json",
    )
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--batch-size", type=int, default=40)
    parser.add_argument("--classification-batch-size", type=int, default=80)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "propose":
        propose(args)


if __name__ == "__main__":
    main()
