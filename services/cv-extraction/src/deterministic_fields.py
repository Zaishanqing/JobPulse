from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from .normalizer import lookup_skill_mapping, skill_mapping_candidates
from .semantic_rules import (
    deterministic_validation_rules,
    language_proficiency_evidence_rules,
)


ENTRY_PREFIXES = {
    "education": "edu",
    "work_experience": "work",
    "project_experience": "proj",
    "languages": "lang",
    "certificates": "cert",
    "awards": "award",
    "publications": "pub",
    "patents": "patent",
    "research_outputs": "research",
    "self_evaluation": "self",
}

_AWARD_LEVEL_PATTERNS = {
    level: re.compile(pattern)
    for level, pattern in deterministic_validation_rules()["award_level_authority"][
        "level_patterns"
    ].items()
}
_AWARD_SHARED_EVIDENCE_SEPARATOR_PATTERN = re.compile(
    deterministic_validation_rules()["award_level_authority"][
        "shared_evidence_separator_pattern"
    ]
)
_LANGUAGE_PROFICIENCY_RULES = language_proficiency_evidence_rules()
_LANGUAGE_PROFICIENCY_LEVEL_PATTERNS = {
    level: re.compile(pattern)
    for level, pattern in _LANGUAGE_PROFICIENCY_RULES["level_patterns"].items()
}
_LANGUAGE_SCORED_CERTIFICATION_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in _LANGUAGE_PROFICIENCY_RULES["scored_certification_patterns"]
)


def _explicit_award_level(text: str) -> str | None:
    matches = [
        (match.start(), match.end(), level)
        for level, pattern in _AWARD_LEVEL_PATTERNS.items()
        for match in pattern.finditer(text)
    ]
    if not matches:
        return None
    last_start = max(item[0] for item in matches)
    last_matches = [item for item in matches if item[0] == last_start]
    levels = {item[2] for item in last_matches}
    if len(levels) != 1:
        raise ValueError(
            f"award_level_authority has conflicting patterns at offset {last_start}: "
            f"{sorted(levels)}"
        )
    return next(iter(levels))

def canonicalize_authoritative_fields(
    payload: dict[str, Any],
    normalization_map: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Apply only exact, uniquely determined taxonomy and evidence authorities."""
    canonicalized = deepcopy(payload)
    corrections: list[dict[str, Any]] = []

    scoped_collections: list[tuple[str, list[Any]]] = [
        ("skills", canonicalized.get("skills", [])),
    ]
    for work_index, work in enumerate(canonicalized.get("work_experience", [])):
        if isinstance(work, dict):
            scoped_collections.append(
                (
                    f"work_experience[{work_index}].tech_stack",
                    work.get("tech_stack", []),
                )
            )
    for project_index, project in enumerate(canonicalized.get("project_experience", [])):
        if isinstance(project, dict):
            scoped_collections.append(
                (
                    f"project_experience[{project_index}].tech_stack",
                    project.get("tech_stack", []),
                )
            )

    for scope, items in scoped_collections:
        for item_index, item in enumerate(items if isinstance(items, list) else []):
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                continue
            if scope != "skills" and item.get("proficiency") == "unknown":
                item["proficiency"] = None
                corrections.append(
                    {
                        "path": f"{scope}[{item_index}].proficiency",
                        "from": "unknown",
                        "to": None,
                        "authority": "experience_proficiency_contract",
                    }
                )
            current = item.get("item_type")
            if isinstance(current, str) and lookup_skill_mapping(
                normalization_map, item["name"], current
            ) is not None:
                continue
            candidates = skill_mapping_candidates(normalization_map, item["name"])
            expected_types = sorted(
                {
                    mapping.get("category_code")
                    for mapping in candidates
                    if isinstance(mapping, dict)
                    and isinstance(mapping.get("category_code"), str)
                }
            )
            if len(expected_types) != 1 or current == expected_types[0]:
                continue
            item["item_type"] = expected_types[0]
            corrections.append(
                {
                    "path": f"{scope}[{item_index}].item_type",
                    "from": current,
                    "to": expected_types[0],
                    "authority": "normalization_map",
                }
            )
    awards = canonicalized.get("awards", [])
    for award_index, award in enumerate(awards if isinstance(awards, list) else []):
        if not isinstance(award, dict):
            continue
        evidence = award.get("evidence")
        quote = evidence.get("quote") if isinstance(evidence, dict) else None
        if not isinstance(quote, str):
            continue
        name = award.get("name")
        expected_level = (
            _explicit_award_level(name) if isinstance(name, str) else None
        )
        if (
            expected_level is None
            and _AWARD_SHARED_EVIDENCE_SEPARATOR_PATTERN.search(quote) is None
        ):
            expected_level = _explicit_award_level(quote)
        current_level = award.get("level")
        if current_level == expected_level:
            continue
        award["level"] = expected_level
        corrections.append(
            {
                "path": f"awards[{award_index}].level",
                "from": current_level,
                "to": expected_level,
                "authority": "award_level_evidence",
            }
        )
    languages = canonicalized.get("languages", [])
    for language_index, language in enumerate(
        languages if isinstance(languages, list) else []
    ):
        if not isinstance(language, dict):
            continue
        current_level = language.get("proficiency")
        level_pattern = _LANGUAGE_PROFICIENCY_LEVEL_PATTERNS.get(current_level)
        evidence = language.get("evidence")
        quote = evidence.get("quote") if isinstance(evidence, dict) else None
        if current_level == "unknown" or level_pattern is None or not isinstance(quote, str):
            continue
        if level_pattern.search(quote) is not None or any(
            pattern.search(quote) is not None
            for pattern in _LANGUAGE_SCORED_CERTIFICATION_PATTERNS
        ):
            continue
        language["proficiency"] = "unknown"
        corrections.append(
            {
                "path": f"languages[{language_index}].proficiency",
                "from": current_level,
                "to": "unknown",
                "authority": "language_proficiency_evidence",
            }
        )
    return canonicalized, corrections


def _initialize_evidence(value: Any) -> None:
    if isinstance(value, dict):
        evidence = value.get("evidence")
        if isinstance(evidence, dict):
            evidence.update(
                {
                    "start": None,
                    "end": None,
                    "alignment": "unresolved",
                    "occurrence_index": None,
                }
            )
        for child in value.values():
            _initialize_evidence(child)
    elif isinstance(value, list):
        for child in value:
            _initialize_evidence(child)


def populate_deterministic_fields(payload: dict, document_id: str) -> dict:
    payload["document_id"] = document_id

    for collection, prefix in ENTRY_PREFIXES.items():
        for index, entry in enumerate(payload.get(collection, []) or [], start=1):
            if isinstance(entry, dict):
                entry["entry_id"] = f"{prefix}_{index:03d}"

    skill_index = 0
    for item in payload.get("skills", []) or []:
        if isinstance(item, dict):
            skill_index += 1
            item["item_id"] = f"skill_{skill_index:03d}"
    for work in payload.get("work_experience", []) or []:
        if not isinstance(work, dict):
            continue
        for item in work.get("tech_stack", []) or []:
            if isinstance(item, dict):
                skill_index += 1
                item["item_id"] = f"skill_{skill_index:03d}"
    for project in payload.get("project_experience", []) or []:
        if not isinstance(project, dict):
            continue
        for item in project.get("tech_stack", []) or []:
            if isinstance(item, dict):
                skill_index += 1
                item["item_id"] = f"skill_{skill_index:03d}"

    _initialize_evidence(payload)
    return payload
