from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any
import re
import unicodedata

import yaml

from .exceptions import InputFormatError
from .models import (
    JDExtractionResult,
    JDNormalizedResult,
    NormalizedRequirement,
    NormalizedSkill,
    SkillRequirement,
    ToolRequirement,
)
from .salary_parser import parse_salary


JOB_FAMILY_RULE_FIELDS = {
    "family_name",
    "priority",
    "role_matchers",
    "technology_keywords",
}
SKILL_CATEGORY_CODES = {
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


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            hash(key)
        except TypeError as error:
            raise InputFormatError(
                f"YAML mapping key must be scalar: {key!r}"
            ) from error
        if key in mapping:
            raise InputFormatError(f"Duplicate YAML key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _validate_skill_identity_consistency(skills: dict[str, Any]) -> None:
    metadata_by_skill_id: dict[str, tuple[str, str, str | None]] = {}
    skill_id_by_canonical_name: dict[str, str] = {}
    for alias, mapping in skills.items():
        if not isinstance(mapping, dict):
            continue
        for required_field in ("skill_id", "canonical_name", "category_code"):
            if (
                not isinstance(mapping.get(required_field), str)
                or not mapping[required_field].strip()
            ):
                raise InputFormatError(
                    f"Skill alias {alias!r} is missing non-empty {required_field}"
                )
        if mapping["category_code"] not in SKILL_CATEGORY_CODES:
            raise InputFormatError(
                f"Skill alias {alias!r} has invalid category_code {mapping['category_code']!r}"
            )
        if _exact_normalization_key(alias) != alias:
            raise InputFormatError(
                f"Skill alias {alias!r} is not NFKC/whitespace normalized"
            )
        if (
            _exact_normalization_key(mapping["canonical_name"])
            != mapping["canonical_name"]
        ):
            raise InputFormatError(
                f"Canonical skill name {mapping['canonical_name']!r} is not NFKC/whitespace normalized"
            )
        skill_id = mapping["skill_id"]
        metadata = (
            mapping["canonical_name"],
            mapping["category_code"],
            mapping.get("subcategory_code"),
        )
        previous_metadata = metadata_by_skill_id.setdefault(skill_id, metadata)
        if previous_metadata != metadata:
            raise InputFormatError(
                f"Skill id {skill_id!r} has inconsistent taxonomy metadata"
            )
        canonical_key = _exact_normalization_key(mapping["canonical_name"])
        previous_skill_id = skill_id_by_canonical_name.setdefault(
            canonical_key, skill_id
        )
        if previous_skill_id != skill_id:
            raise InputFormatError(
                f"Canonical skill name {mapping['canonical_name']!r} belongs to multiple skill ids"
            )


def _validate_keyword_list(family_code: str, field: str, value: Any) -> None:
    if (
        not isinstance(value, list)
        or any(not isinstance(keyword, str) or not keyword.strip() for keyword in value)
        or len({_title_match_key(keyword) for keyword in value}) != len(value)
    ):
        raise InputFormatError(
            f"Job family {family_code!r} field {field!r} must be a list of unique non-empty strings"
        )


def _validate_role_matchers(family_code: str, value: Any) -> None:
    if not isinstance(value, list):
        raise InputFormatError(
            f"Job family {family_code!r} role_matchers must be a list"
        )
    for matcher in value:
        if not isinstance(matcher, list) or not matcher:
            raise InputFormatError(
                f"Job family {family_code!r} role_matchers must contain non-empty keyword groups"
            )
        for group in matcher:
            _validate_keyword_list(family_code, "role_matchers", group)
            if not group:
                raise InputFormatError(
                    f"Job family {family_code!r} role matcher groups cannot be empty"
                )


def _validate_job_families(job_families: dict[str, Any]) -> None:
    for family_code, rule in job_families.items():
        if not isinstance(family_code, str) or not family_code:
            raise InputFormatError("Job family codes must be non-empty strings")
        if not isinstance(rule, dict) or set(rule) != JOB_FAMILY_RULE_FIELDS:
            raise InputFormatError(
                f"Job family {family_code!r} must define exactly: "
                + ", ".join(sorted(JOB_FAMILY_RULE_FIELDS))
            )
        if not isinstance(rule["family_name"], str) or not rule["family_name"].strip():
            raise InputFormatError(
                f"Job family {family_code!r} must define a non-empty family_name"
            )
        if isinstance(rule["priority"], bool) or not isinstance(rule["priority"], int):
            raise InputFormatError(
                f"Job family {family_code!r} priority must be an integer"
            )
        _validate_role_matchers(family_code, rule["role_matchers"])
        _validate_keyword_list(
            family_code, "technology_keywords", rule["technology_keywords"]
        )
        if not rule["role_matchers"] and not rule["technology_keywords"]:
            raise InputFormatError(
                f"Job family {family_code!r} must define role_matchers or technology_keywords"
            )


def load_normalization_map(path: str) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        raise InputFormatError(f"Normalization file does not exist: {file_path}")
    payload = (
        yaml.load(file_path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader) or {}
    )
    if (
        not isinstance(payload, dict)
        or payload.get("version") != "2.0"
        or not isinstance(payload.get("skills"), dict)
        or not isinstance(payload.get("position_taxonomy_version"), str)
    ):
        raise InputFormatError(
            "Normalization config must be V2 with skills and position_taxonomy_version"
        )
    exact_typed_skills: dict[tuple[str, str], dict[str, Any]] = {}
    exact_mappings_by_name: dict[str, list[dict[str, Any]]] = {}
    typed_skills: dict[tuple[str, str], dict[str, Any]] = {}
    mappings_by_name: dict[str, list[dict[str, Any]]] = {}
    aliases_by_exact_typed_key: dict[tuple[str, str], str] = {}
    aliases_by_typed_key: dict[tuple[str, str], str] = {}
    aliases_missing_category: list[str] = []
    for alias, mapping in payload["skills"].items():
        if not isinstance(alias, str) or not isinstance(mapping, dict):
            raise InputFormatError(
                "Normalization skill aliases must map strings to objects"
            )
        exact_key = _exact_normalization_key(alias)
        key = _normalization_key(alias)
        category_code = mapping.get("category_code")
        if not isinstance(category_code, str) and key in mappings_by_name:
            previous = mappings_by_name[key][0]
            if previous != mapping:
                previous_alias = next(
                    name
                    for (_, category), name in aliases_by_typed_key.items()
                    if _normalization_key(name) == key and category == ""
                )
                raise InputFormatError(
                    f"Conflicting normalization aliases: {previous_alias!r} and {alias!r}"
                )
        if not isinstance(category_code, str) or not category_code:
            mappings_by_name.setdefault(key, []).append(mapping)
            aliases_by_typed_key[(key, "")] = alias
            aliases_missing_category.append(alias)
            continue
        exact_typed_key = (exact_key, category_code)
        exact_existing = exact_typed_skills.get(exact_typed_key)
        if exact_existing is not None and exact_existing != mapping:
            raise InputFormatError(
                f"Conflicting exact normalization aliases: "
                f"{aliases_by_exact_typed_key[exact_typed_key]!r} and {alias!r}"
            )
        exact_typed_skills[exact_typed_key] = mapping
        aliases_by_exact_typed_key[exact_typed_key] = alias
        exact_name_mappings = exact_mappings_by_name.setdefault(exact_key, [])
        if mapping not in exact_name_mappings:
            exact_name_mappings.append(mapping)
        typed_key = (key, category_code)
        existing = typed_skills.get(typed_key)
        if existing is not None and existing != mapping:
            raise InputFormatError(
                f"Conflicting normalization aliases: {aliases_by_typed_key[typed_key]!r} and {alias!r}"
            )
        typed_skills[typed_key] = mapping
        aliases_by_typed_key[typed_key] = alias
        name_mappings = mappings_by_name.setdefault(key, [])
        if mapping not in name_mappings:
            name_mappings.append(mapping)
    if aliases_missing_category:
        raise InputFormatError(
            f"Normalization skill alias {aliases_missing_category[0]!r} is missing category_code"
        )
    source_type_overrides = payload.get("skill_source_type_overrides", {})
    if not isinstance(source_type_overrides, dict):
        raise InputFormatError("skill_source_type_overrides must be an object")
    for alias, source_types in source_type_overrides.items():
        if alias not in payload["skills"]:
            raise InputFormatError(
                f"Skill source type override references unknown alias {alias!r}"
            )
        if (
            not isinstance(source_types, list)
            or not source_types
            or any(
                not isinstance(source_type, str)
                or source_type not in SKILL_CATEGORY_CODES
                for source_type in source_types
            )
            or len(set(source_types)) != len(source_types)
        ):
            raise InputFormatError(
                f"Skill source type override for {alias!r} must contain unique legal item types"
            )
        mapping = payload["skills"][alias]
        exact_key = _exact_normalization_key(alias)
        key = _normalization_key(alias)
        for source_type in source_types:
            exact_typed_key = (exact_key, source_type)
            exact_existing = exact_typed_skills.get(exact_typed_key)
            if exact_existing is not None and exact_existing != mapping:
                raise InputFormatError(
                    f"Conflicting exact source type override for {alias!r}"
                )
            exact_typed_skills[exact_typed_key] = mapping
            typed_key = (key, source_type)
            existing = typed_skills.get(typed_key)
            if existing is not None and existing != mapping:
                raise InputFormatError(
                    f"Conflicting source type override for {alias!r}"
                )
            typed_skills[typed_key] = mapping
    _validate_skill_identity_consistency(payload["skills"])
    payload["_skills_by_exact_typed_key"] = exact_typed_skills
    payload["_skill_mappings_by_exact_key"] = exact_mappings_by_name
    payload["_skills_by_typed_normalized_key"] = typed_skills
    payload["_skill_mappings_by_normalized_key"] = mappings_by_name
    payload["_skills_by_normalized_key"] = {
        key: mappings[0]
        for key, mappings in mappings_by_name.items()
        if len(mappings) == 1
    }
    return payload


def _normalization_key(value: str) -> str:
    return _exact_normalization_key(value).casefold()


def _exact_normalization_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def skill_mapping_candidates(
    normalization_map: dict[str, Any],
    name: str,
) -> list[dict[str, Any]]:
    exact_key = _exact_normalization_key(name)
    exact_mappings = normalization_map.get("_skill_mappings_by_exact_key", {})
    if exact_key in exact_mappings:
        return [
            mapping
            for mapping in exact_mappings[exact_key]
            if isinstance(mapping, dict)
        ]
    folded = normalization_map.get("_skill_mappings_by_normalized_key", {}).get(
        _normalization_key(name), []
    )
    candidates = [mapping for mapping in folded if isinstance(mapping, dict)]
    return candidates if len(candidates) == 1 else []


def _title_match_key(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKC", value).casefold()
        if character.isalnum()
    )


def _normalized_title_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(
        "".join(
            character if character.isalnum() else " " for character in normalized
        ).split()
    )


@lru_cache(maxsize=None)
def _title_keyword_pattern(keyword: str) -> re.Pattern[str]:
    segments: list[tuple[str, bool]] = []
    separated = False
    for character in unicodedata.normalize("NFKC", keyword).casefold():
        if not character.isalnum():
            separated = bool(segments)
            continue
        is_ascii = character.isascii()
        if segments and segments[-1][1] == is_ascii and not separated:
            segments[-1] = (segments[-1][0] + character, is_ascii)
        else:
            segments.append((character, is_ascii))
        separated = False
    parts = [
        rf"(?<![a-z0-9]){re.escape(segment)}(?![a-z0-9])"
        if is_ascii
        else re.escape(segment)
        for segment, is_ascii in segments
    ]
    return re.compile(r"\s*".join(parts))


def _matches_any_title_keyword(title_text: str, keywords: list[str]) -> bool:
    return any(
        _title_keyword_pattern(keyword).search(title_text) is not None
        for keyword in keywords
    )


def _matches_role_matcher(title_text: str, matcher: list[list[str]]) -> bool:
    return all(_matches_any_title_keyword(title_text, group) for group in matcher)


def classify_job_title(
    title: str | None, normalization_map: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema_version": "job-position-classification.v3",
        "taxonomy_version": normalization_map["position_taxonomy_version"],
        "source_title": title,
        "classification_status": "catalog_gap",
        "review_reason_codes": ["CLASSIFICATION_NOT_RUN"],
        "classification_policy_version": "position-classifier.v3.0",
    }


def lookup_skill_mapping(
    normalization_map: dict[str, Any],
    name: str,
    item_type: str,
) -> dict[str, Any] | None:
    exact_key = _exact_normalization_key(name)
    exact_mappings = normalization_map.get("_skill_mappings_by_exact_key", {})
    exact_typed = normalization_map.get("_skills_by_exact_typed_key", {})
    if exact_key in exact_mappings:
        mapping = exact_typed.get((exact_key, item_type))
        return mapping if isinstance(mapping, dict) else None
    candidates = skill_mapping_candidates(normalization_map, name)
    if len(candidates) == 1:
        candidate = candidates[0]
        if candidate.get("category_code") == item_type:
            return candidate
        typed_override = normalization_map.get(
            "_skills_by_typed_normalized_key", {}
        ).get((_normalization_key(name), item_type))
        return typed_override if isinstance(typed_override, dict) else None
    return None


def normalize_extraction(
    extraction: JDExtractionResult,
    normalization_map: dict[str, Any],
    jd_text: str,
) -> JDNormalizedResult:
    normalized_requirements: list[NormalizedRequirement] = []
    unresolved: list[str] = []
    for requirement in extraction.requirements:
        skills: list[NormalizedSkill] = []
        if isinstance(requirement, SkillRequirement):
            for item in requirement.items:
                mapped = lookup_skill_mapping(
                    normalization_map, item.name, item.item_type
                )
                status = "resolved" if isinstance(mapped, dict) else "unresolved"
                if status == "unresolved":
                    unresolved.append(item.name)
                skills.append(
                    NormalizedSkill(
                        source_name=item.name,
                        skill_id=mapped.get("skill_id")
                        if isinstance(mapped, dict)
                        else None,
                        canonical_name=mapped.get("canonical_name")
                        if isinstance(mapped, dict)
                        else None,
                        category_code=mapped.get("category_code", item.item_type)
                        if isinstance(mapped, dict)
                        else item.item_type,
                        subcategory_code=mapped.get("subcategory_code")
                        if isinstance(mapped, dict)
                        else None,
                        resolution_status=status,
                    )
                )
        elif isinstance(requirement, ToolRequirement):
            for name in requirement.tools:
                mapped = lookup_skill_mapping(normalization_map, name, "tool")
                status = "resolved" if isinstance(mapped, dict) else "unresolved"
                if status == "unresolved":
                    unresolved.append(name)
                skills.append(
                    NormalizedSkill(
                        source_name=name,
                        skill_id=mapped.get("skill_id")
                        if isinstance(mapped, dict)
                        else None,
                        canonical_name=mapped.get("canonical_name")
                        if isinstance(mapped, dict)
                        else None,
                        category_code=mapped.get("category_code", "tool")
                        if isinstance(mapped, dict)
                        else "tool",
                        subcategory_code=mapped.get("subcategory_code")
                        if isinstance(mapped, dict)
                        else None,
                        resolution_status=status,
                    )
                )
        normalized_requirements.append(
            NormalizedRequirement(
                requirement_id=requirement.requirement_id,
                kind=requirement.kind,
                modality=requirement.modality,
                skills=skills,
            )
        )
    title = extraction.job_title.value if extraction.job_title is not None else None
    job_classification = classify_job_title(title, normalization_map)
    salary_payload = parse_salary(jd_text)
    return JDNormalizedResult(
        document_id=extraction.document_id,
        job_classification=job_classification,
        normalized_requirements=normalized_requirements,
        salary=salary_payload,
        unresolved_items=unresolved,
    )
