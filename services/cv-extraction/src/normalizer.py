from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator
import unicodedata

import yaml

from .exceptions import InputFormatError
from .models import CVExtractionResult, CVNormalizedResult, NormalizedSkill, SkillItem


SKILL_CATEGORY_CODES = {
    "programming_language", "framework", "library", "database", "tool", "platform",
    "methodology", "domain_knowledge", "other",
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
            raise InputFormatError(f"YAML mapping key must be scalar: {key!r}") from error
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
        skill_id = mapping["skill_id"]
        if mapping["category_code"] not in SKILL_CATEGORY_CODES:
            raise InputFormatError(
                f"Skill alias {alias!r} has invalid category_code {mapping['category_code']!r}"
            )
        if _exact_normalization_key(alias) != alias:
            raise InputFormatError(f"Skill alias {alias!r} is not NFKC/whitespace normalized")
        if _exact_normalization_key(mapping["canonical_name"]) != mapping["canonical_name"]:
            raise InputFormatError(
                f"Canonical skill name {mapping['canonical_name']!r} is not NFKC/whitespace normalized"
            )
        metadata = (
            mapping["canonical_name"], mapping["category_code"], mapping.get("subcategory_code")
        )
        previous_metadata = metadata_by_skill_id.setdefault(skill_id, metadata)
        if previous_metadata != metadata:
            raise InputFormatError(f"Skill id {skill_id!r} has inconsistent taxonomy metadata")
        canonical_key = _exact_normalization_key(mapping["canonical_name"])
        previous_skill_id = skill_id_by_canonical_name.setdefault(canonical_key, skill_id)
        if previous_skill_id != skill_id:
            raise InputFormatError(
                f"Canonical skill name {mapping['canonical_name']!r} belongs to multiple skill ids"
            )


def load_normalization_map(path: str) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        raise InputFormatError(f"Normalization file does not exist: {file_path}")
    payload = yaml.load(file_path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader) or {}
    if (
        not isinstance(payload, dict)
        or payload.get("version") != "2.0"
        or not isinstance(payload.get("skills"), dict)
    ):
        raise InputFormatError("Normalization config must be V2 with a skills mapping")
    resolved_confidence = payload.get("resolved_mapping_confidence")
    if (
        isinstance(resolved_confidence, bool)
        or not isinstance(resolved_confidence, (int, float))
        or not 0 <= float(resolved_confidence) <= 1
    ):
        raise InputFormatError(
            "Normalization config must declare resolved_mapping_confidence in [0, 1]"
        )

    exact_typed_skills: dict[tuple[str, str], dict[str, Any]] = {}
    exact_mappings_by_name: dict[str, list[dict[str, Any]]] = {}
    typed_skills: dict[tuple[str, str], dict[str, Any]] = {}
    mappings_by_name: dict[str, list[dict[str, Any]]] = {}
    aliases_by_exact_typed_key: dict[tuple[str, str], str] = {}
    aliases_by_typed_key: dict[tuple[str, str], str] = {}
    for alias, mapping in payload["skills"].items():
        if not isinstance(alias, str) or not alias.strip() or not isinstance(mapping, dict):
            raise InputFormatError("Normalization skill aliases must map non-empty strings to objects")
        for required_field in ("category_code", "skill_id", "canonical_name"):
            if not isinstance(mapping.get(required_field), str) or not mapping[required_field].strip():
                raise InputFormatError(
                    f"Skill alias {alias!r} is missing non-empty {required_field}"
                )
        exact_key = (_exact_normalization_key(alias), mapping["category_code"])
        exact_existing = exact_typed_skills.get(exact_key)
        if exact_existing is not None and exact_existing != mapping:
            raise InputFormatError(
                f"Conflicting exact normalization aliases: "
                f"{aliases_by_exact_typed_key[exact_key]!r} and {alias!r}"
            )
        exact_typed_skills[exact_key] = mapping
        aliases_by_exact_typed_key[exact_key] = alias
        exact_name_mappings = exact_mappings_by_name.setdefault(exact_key[0], [])
        if mapping not in exact_name_mappings:
            exact_name_mappings.append(mapping)
        key = (_normalization_key(alias), mapping["category_code"])
        existing = typed_skills.get(key)
        if existing is not None and existing != mapping:
            raise InputFormatError(
                f"Conflicting normalization aliases: {aliases_by_typed_key[key]!r} and {alias!r}"
            )
        typed_skills[key] = mapping
        aliases_by_typed_key[key] = alias
        name_mappings = mappings_by_name.setdefault(key[0], [])
        if mapping not in name_mappings:
            name_mappings.append(mapping)
    _validate_skill_identity_consistency(payload["skills"])
    payload["_skills_by_exact_typed_key"] = exact_typed_skills
    payload["_skill_mappings_by_exact_key"] = exact_mappings_by_name
    payload["_skills_by_typed_normalized_key"] = typed_skills
    payload["_skill_mappings_by_normalized_key"] = mappings_by_name
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
        return [mapping for mapping in exact_mappings[exact_key] if isinstance(mapping, dict)]
    folded = normalization_map.get("_skill_mappings_by_normalized_key", {}).get(
        _normalization_key(name), []
    )
    candidates = [mapping for mapping in folded if isinstance(mapping, dict)]
    return candidates if len(candidates) == 1 else []


def lookup_skill_mapping(
    normalization_map: dict[str, Any],
    name: str,
    item_type: str,
) -> dict[str, Any] | None:
    exact_key = _exact_normalization_key(name)
    exact_mappings = normalization_map.get("_skill_mappings_by_exact_key", {})
    if exact_key in exact_mappings:
        mapping = normalization_map["_skills_by_exact_typed_key"].get((exact_key, item_type))
        return mapping if isinstance(mapping, dict) else None
    candidates = skill_mapping_candidates(normalization_map, name)
    if len(candidates) == 1 and candidates[0].get("category_code") == item_type:
        return candidates[0]
    return None


def _iter_skills(extraction: CVExtractionResult) -> Iterator[tuple[str, SkillItem]]:
    for item in extraction.skills:
        yield "skills", item
    for work in extraction.work_experience:
        for item in work.tech_stack:
            yield f"work_experience:{work.entry_id}:tech_stack", item
    for project in extraction.project_experience:
        for item in project.tech_stack:
            yield f"project_experience:{project.entry_id}:tech_stack", item


def normalize_extraction(
    extraction: CVExtractionResult,
    normalization_map: dict[str, Any],
) -> CVNormalizedResult:
    normalized_skills: list[NormalizedSkill] = []
    unresolved: list[str] = []
    for source_scope, item in _iter_skills(extraction):
        if item.item_type in {"soft_skill", "language"}:
            continue
        mapped = lookup_skill_mapping(normalization_map, item.name, item.item_type)
        status = "resolved" if mapped is not None else "unresolved"
        if mapped is None:
            unresolved.append(item.name)
        resolution_source = "unresolved"
        if mapped is not None:
            source_key = _normalization_key(item.name)
            canonical_key = _normalization_key(mapped["canonical_name"])
            resolution_source = "canonical_name" if source_key == canonical_key else "alias"
        normalized_skills.append(
            NormalizedSkill(
                source_item_id=item.item_id,
                source_scope=source_scope,
                source_name=item.name,
                skill_id=mapped["skill_id"] if mapped is not None else None,
                canonical_name=mapped["canonical_name"] if mapped is not None else None,
                category_code=mapped["category_code"] if mapped is not None else item.item_type,
                subcategory_code=mapped.get("subcategory_code") if mapped is not None else None,
                resolution_status=status,
                normalization_confidence=(
                    float(normalization_map["resolved_mapping_confidence"])
                    if mapped is not None
                    else None
                ),
                resolution_source=resolution_source,
            )
        )
    return CVNormalizedResult(
        document_id=extraction.document_id,
        normalized_skills=normalized_skills,
        unresolved_items=unresolved,
    )
