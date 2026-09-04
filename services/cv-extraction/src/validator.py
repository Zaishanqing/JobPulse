from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from typing import Any, Iterator

from pydantic import ValidationError

from .exceptions import InvalidJSONError, SchemaValidationError, SemanticValidationError
from .models import CVExtractionResult, CVNormalizedResult, SkillItem
from .normalizer import lookup_skill_mapping, skill_mapping_candidates
from .review_rules import get_review_rule
from .semantic_rules import (
    deterministic_validation_rules,
    language_proficiency_evidence_rules,
    proficiency_evidence_rules,
    source_coverage_rules,
)
from .skill_semantics import (
    split_composite_skill_name,
    split_taxonomy_confirmed_shared_skill_name,
)


BUSINESS_VALIDATOR_VERSION = "2.21"

_VALIDATION_RULES = deterministic_validation_rules()
_DESCRIPTIVE_RULES = _VALIDATION_RULES["descriptive_skill"]
_PROJECT_NAME_RULES = _VALIDATION_RULES["project_name"]
_CREDENTIAL_AWARD_RULES = _VALIDATION_RULES["credential_award_classification"]
_EXPERIENCE_CLASSIFICATION_RULES = _VALIDATION_RULES["experience_classification"]
PROJECT_NAME_LIST_MARKER_PATTERN = re.compile(_PROJECT_NAME_RULES["leading_marker_pattern"])
PROJECT_NAME_ACTION_PREFIX_PATTERN = re.compile(_PROJECT_NAME_RULES["action_prefix_pattern"])
PROJECT_NAME_ROLE_TITLE_PATTERN = re.compile(_PROJECT_NAME_RULES["role_title_pattern"])
PROJECT_NAME_ACTIVITY_PATTERN = re.compile(_PROJECT_NAME_RULES["activity_title_pattern"])
PROJECT_NAME_COMPETITION_PATTERN = re.compile(_PROJECT_NAME_RULES["competition_title_pattern"])
PROJECT_TECHNICAL_ARTIFACT_PATTERN = re.compile(_PROJECT_NAME_RULES["technical_artifact_pattern"])
PROJECT_ORGANIZATION_NAME_PATTERN = re.compile(
    _PROJECT_NAME_RULES["organization_name_pattern"], re.IGNORECASE
)
PROJECT_ORGANIZATION_ROLE_PATTERN = re.compile(
    _PROJECT_NAME_RULES["organization_role_pattern"], re.IGNORECASE
)
PROJECT_OUTCOME_PHRASE_PATTERN = re.compile(
    _PROJECT_NAME_RULES["outcome_phrase_pattern"]
)
DESCRIPTIVE_SKILL_SUFFIXES = tuple(_DESCRIPTIVE_RULES["suffixes"])
DESCRIPTIVE_SKILL_PREFIXES = tuple(_DESCRIPTIVE_RULES["prefixes"])
PROJECT_NAME_SENTENCE_ENDINGS = tuple(_PROJECT_NAME_RULES["sentence_endings"])
_PROFICIENCY_EVIDENCE_RULES = proficiency_evidence_rules()
_PROFICIENCY_LEVEL_PATTERNS = {
    level: re.compile(pattern)
    for level, pattern in _PROFICIENCY_EVIDENCE_RULES["level_patterns"].items()
}
_PROFICIENCY_ENFORCED_SCOPES = frozenset(
    _PROFICIENCY_EVIDENCE_RULES["enforced_scopes"]
)
_LANGUAGE_PROFICIENCY_EVIDENCE_RULES = language_proficiency_evidence_rules()
_LANGUAGE_PROFICIENCY_LEVEL_PATTERNS = {
    level: re.compile(pattern)
    for level, pattern in _LANGUAGE_PROFICIENCY_EVIDENCE_RULES["level_patterns"].items()
}
_LANGUAGE_SCORED_CERTIFICATION_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in _LANGUAGE_PROFICIENCY_EVIDENCE_RULES["scored_certification_patterns"]
)
_AWARD_IDENTITY_PATTERNS = tuple(
    re.compile(pattern) for pattern in _CREDENTIAL_AWARD_RULES["award_identity_patterns"]
)
_CREDENTIAL_IDENTITY_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in _CREDENTIAL_AWARD_RULES["credential_identity_patterns"]
)
_AWARD_PROJECT_METRIC_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in _CREDENTIAL_AWARD_RULES["project_metric_only_patterns"]
)
_AWARD_RESULT_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in _CREDENTIAL_AWARD_RULES["award_result_patterns"]
)
_VAGUE_AWARD_NAME_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in _CREDENTIAL_AWARD_RULES["vague_award_name_patterns"]
)
_AWARD_IDENTITY_SEPARATOR_PATTERN = re.compile(
    _CREDENTIAL_AWARD_RULES["award_identity_separator_pattern"]
)
_FORBIDDEN_MODEL_CERTIFICATE_KINDS = frozenset(
    _CREDENTIAL_AWARD_RULES["forbidden_model_certificate_kinds"]
)
_RESEARCH_PROJECT_SOURCE_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in _EXPERIENCE_CLASSIFICATION_RULES["research_project_source_patterns"]
)
_AWARD_LEVEL_PATTERNS = {
    level: re.compile(pattern)
    for level, pattern in _VALIDATION_RULES["award_level_authority"][
        "level_patterns"
    ].items()
}
_AWARD_SEMANTIC_SCOPE_STRIP_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in _VALIDATION_RULES["award_level_authority"][
        "semantic_scope_strip_patterns"
    ]
)
_LOCATION_INSTITUTION_SUFFIX_PATTERN = re.compile(
    r"(?:省|自治区|市|自治州|州|县|区).*(?:大学|学院|研究院|实验室)$"
)


def validate_schema(data: dict) -> CVExtractionResult:
    if not isinstance(data, dict):
        raise InvalidJSONError("Model output must be a JSON object")
    try:
        return CVExtractionResult.model_validate(data)
    except ValidationError as exc:
        raise SchemaValidationError(
            f"Schema validation failed: {exc}",
            errors=exc.errors(include_url=False),
        ) from exc


def _iter_skills(result: CVExtractionResult) -> Iterator[SkillItem]:
    yield from result.skills
    for work in result.work_experience:
        yield from work.tech_stack
    for project in result.project_experience:
        yield from project.tech_stack


def _project_name_shape_reasons(name: str) -> list[str]:
    stripped = name.strip()
    reasons: list[str] = []
    if PROJECT_NAME_LIST_MARKER_PATTERN.match(stripped):
        reasons.append("leading_list_marker")
    if stripped.endswith(PROJECT_NAME_SENTENCE_ENDINGS):
        reasons.append("sentence_ending_punctuation")
    if (
        PROJECT_NAME_ACTION_PREFIX_PATTERN.match(stripped)
        and PROJECT_TECHNICAL_ARTIFACT_PATTERN.fullmatch(stripped) is None
    ):
        reasons.append("action_clause_instead_of_identifier")
    if PROJECT_NAME_ROLE_TITLE_PATTERN.fullmatch(stripped):
        reasons.append("role_title_instead_of_project_identifier")
    if PROJECT_NAME_ACTIVITY_PATTERN.fullmatch(stripped):
        reasons.append("activity_title_instead_of_project_identifier")
    if PROJECT_OUTCOME_PHRASE_PATTERN.fullmatch(stripped):
        reasons.append("outcome_phrase_instead_of_project_identifier")
    return reasons


def _is_organization_role_project(name: str, role: object) -> bool:
    return (
        isinstance(role, str)
        and PROJECT_ORGANIZATION_NAME_PATTERN.fullmatch(name.strip()) is not None
        and PROJECT_ORGANIZATION_ROLE_PATTERN.fullmatch(role.strip()) is not None
    )


def _skill_shape_violations(
    *, item_id: str, name: str, item_type: str, skill_index: int
) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    parts = split_composite_skill_name(name)
    if parts is not None:
        violations.append(
            {
                "code": (
                    "invalid_numeric_skill_component"
                    if any(part.isdecimal() for part in parts)
                    else "composite_skill_item"
                ),
                "item_id": item_id,
                "skill_index": skill_index,
                "name": name,
                "parts": parts,
            }
        )
    if (
        name.endswith(DESCRIPTIVE_SKILL_SUFFIXES)
        or name.startswith(DESCRIPTIVE_SKILL_PREFIXES)
    ):
        violations.append(
            {
                "code": "descriptive_skill_item",
                "item_id": item_id,
                "skill_index": skill_index,
                "name": name,
                "item_type": item_type,
            }
        )
    return violations


def validate_semantic_constraints(result: CVExtractionResult) -> None:
    violations: list[dict[str, Any]] = []

    for index, item in enumerate(_iter_skills(result)):
        violations.extend(
            _skill_shape_violations(
                item_id=item.item_id,
                name=item.name,
                item_type=item.item_type,
                skill_index=index,
            )
        )

    _check_duplicate_education(violations, result)
    _check_duplicate_work(violations, result)
    _check_duplicate_projects(violations, result)
    _check_duplicate_awards(violations, result)
    _check_project_name_shape(violations, result)
    _check_competition_project_names(violations, result)
    _check_work_project_classification(violations, result)
    _check_credential_award_classification(violations, result)
    _check_award_name_quality(violations, result)
    _check_match_field_evidence(violations, result)
    _check_experience_skill_proficiency(violations, result)
    _check_language_proficiency(violations, result)
    _check_personal_location_shape(violations, result)

    if violations:
        raise SemanticValidationError(
            "Deterministic semantic validation failed: "
            + json.dumps(violations, ensure_ascii=False, separators=(",", ":")),
            violations=violations,
        )


def _check_credential_award_classification(
    violations: list[dict[str, Any]], result: CVExtractionResult
) -> None:
    for entry in result.certificates:
        name_has_award_identity = any(
            pattern.search(entry.name) is not None
            for pattern in _AWARD_IDENTITY_PATTERNS
        )
        name_has_credential_identity = any(
            pattern.search(entry.name) is not None
            for pattern in _CREDENTIAL_IDENTITY_PATTERNS
        )
        evidence_has_award_identity = any(
            pattern.search(entry.evidence.quote) is not None
            for pattern in _AWARD_IDENTITY_PATTERNS
        )
        if (
            entry.kind in _FORBIDDEN_MODEL_CERTIFICATE_KINDS
            or name_has_award_identity
            or (not name_has_credential_identity and evidence_has_award_identity)
        ):
            violations.append(
                {
                    "code": "credential_award_misclassified",
                    "entry_id": entry.entry_id,
                    "source_collection": "certificates",
                    "expected_collection": "awards",
                    "source_id": entry.evidence.source_id,
                }
            )
    for entry in result.awards:
        if any(
            pattern.fullmatch(entry.name.strip())
            for pattern in _AWARD_PROJECT_METRIC_PATTERNS
        ):
            violations.append(
                {
                    "code": "project_metric_as_award",
                    "entry_id": entry.entry_id,
                    "source_collection": "awards",
                    "source_id": entry.evidence.source_id,
                    "name": entry.name,
                }
            )
        name_has_award_identity = any(
            pattern.search(entry.name) is not None
            for pattern in _AWARD_IDENTITY_PATTERNS
        )
        name_has_credential_identity = any(
            pattern.search(entry.name) is not None
            for pattern in _CREDENTIAL_IDENTITY_PATTERNS
        )
        evidence_has_award_identity = any(
            pattern.search(entry.evidence.quote) is not None
            for pattern in _AWARD_IDENTITY_PATTERNS
        )
        evidence_has_credential_identity = any(
            pattern.search(entry.evidence.quote) is not None
            for pattern in _CREDENTIAL_IDENTITY_PATTERNS
        )
        if (
            name_has_credential_identity
            and not name_has_award_identity
        ) or (
            not name_has_award_identity
            and not name_has_credential_identity
            and evidence_has_credential_identity
            and not evidence_has_award_identity
        ):
            violations.append(
                {
                    "code": "credential_award_misclassified",
                    "entry_id": entry.entry_id,
                    "source_collection": "awards",
                    "expected_collection": "certificates",
                    "source_id": entry.evidence.source_id,
                }
            )


def _check_award_name_quality(
    violations: list[dict[str, Any]], result: CVExtractionResult
) -> None:
    for entry in result.awards:
        if any(
            pattern.fullmatch(entry.name.strip())
            for pattern in _VAGUE_AWARD_NAME_PATTERNS
        ):
            violations.append(
                {
                    "code": "vague_award_name",
                    "entry_id": entry.entry_id,
                    "name": entry.name,
                    "source_id": entry.evidence.source_id,
                }
            )
            continue
        evidence_segments = _AWARD_IDENTITY_SEPARATOR_PATTERN.split(
            entry.evidence.quote
        )
        evidence_segment = next(
            (segment for segment in evidence_segments if entry.name in segment),
            entry.evidence.quote,
        )
        evidence_has_result = any(
            pattern.search(evidence_segment) is not None
            for pattern in _AWARD_RESULT_PATTERNS
        )
        name_has_result = any(
            pattern.search(entry.name) is not None
            for pattern in _AWARD_RESULT_PATTERNS
        )
        if evidence_has_result and not name_has_result:
            violations.append(
                {
                    "code": "award_name_missing_result",
                    "entry_id": entry.entry_id,
                    "name": entry.name,
                    "source_id": entry.evidence.source_id,
                    "source_text": entry.evidence.quote,
                }
            )


def _check_work_project_classification(
    violations: list[dict[str, Any]], result: CVExtractionResult
) -> None:
    for entry in result.work_experience:
        if any(
            pattern.fullmatch(entry.evidence.quote.strip()) is not None
            for pattern in _RESEARCH_PROJECT_SOURCE_PATTERNS
        ):
            violations.append(
                {
                    "code": "research_project_as_work",
                    "entry_id": entry.entry_id,
                    "source_collection": "work_experience",
                    "expected_collection": "project_experience",
                    "source_id": entry.evidence.source_id,
                }
            )


def _check_experience_skill_proficiency(
    violations: list[dict[str, Any]], result: CVExtractionResult
) -> None:
    scoped_entries = (
        ("work_experience", entry.entry_id, entry.tech_stack)
        for entry in result.work_experience
    )
    scoped_entries = (
        *scoped_entries,
        *(
            ("project_experience", entry.entry_id, entry.tech_stack)
            for entry in result.project_experience
        ),
    )
    for scope, entry_id, skills in scoped_entries:
        if scope not in _PROFICIENCY_ENFORCED_SCOPES:
            continue
        for item in skills:
            if item.proficiency in (None, "unknown"):
                continue
            pattern = _PROFICIENCY_LEVEL_PATTERNS[item.proficiency]
            if pattern.search(item.evidence.quote) is not None:
                continue
            violations.append(
                {
                    "code": "unsupported_experience_skill_proficiency",
                    "entry_id": entry_id,
                    "item_id": item.item_id,
                    "scope": scope,
                    "name": item.name,
                    "proficiency": item.proficiency,
                    "evidence_quote": item.evidence.quote,
                }
            )


def _check_language_proficiency(
    violations: list[dict[str, Any]], result: CVExtractionResult
) -> None:
    for entry in result.languages:
        if entry.proficiency == "unknown":
            continue
        quote = entry.evidence.quote
        level_pattern = _LANGUAGE_PROFICIENCY_LEVEL_PATTERNS[entry.proficiency]
        if level_pattern.search(quote) is not None or any(
            pattern.search(quote) is not None
            for pattern in _LANGUAGE_SCORED_CERTIFICATION_PATTERNS
        ):
            continue
        violations.append(
            {
                "code": "unsupported_language_proficiency",
                "entry_id": entry.entry_id,
                "language": entry.language,
                "proficiency": entry.proficiency,
                "source_id": entry.evidence.source_id,
                "evidence_quote": quote,
            }
        )


def _evidence_source_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        if isinstance(value.get("source_id"), str) and isinstance(value.get("quote"), str):
            found.add(value["source_id"])
        for child in value.values():
            found.update(_evidence_source_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_evidence_source_ids(child))
    return found


def collect_source_coverage_requirements(
    source_blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """List source blocks that the final section-coverage gate already requires."""
    rules = source_coverage_rules()
    heading_patterns = {
        section: tuple(re.compile(pattern) for pattern in patterns)
        for section, patterns in rules["heading_patterns"].items()
    }
    inline_transition_patterns = {
        section: tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns)
        for section, patterns in rules["inline_section_transition_patterns"].items()
    }
    enforced_sections = {
        section: tuple(collections)
        for section, collections in rules["enforced_sections"].items()
    }
    ignored_patterns = tuple(
        re.compile(pattern) for pattern in rules["ignored_content_patterns"]
    )
    required_patterns = tuple(
        re.compile(pattern) for pattern in rules["required_content_patterns"]
    )
    minimum_characters = int(rules["minimum_content_characters"])
    requirements: list[dict[str, Any]] = []
    active_section: str | None = None
    for block in source_blocks:
        text = str(block.get("text", "")).strip()
        heading_text = text.rstrip(":：").strip()
        matched_section = next(
            (
                section
                for section, patterns in heading_patterns.items()
                if any(pattern.fullmatch(heading_text) is not None for pattern in patterns)
            ),
            None,
        )
        if matched_section is not None:
            active_section = matched_section
            continue
        inline_section = next(
            (
                section
                for section, patterns in inline_transition_patterns.items()
                if any(pattern.fullmatch(text) is not None for pattern in patterns)
            ),
            None,
        )
        if inline_section is not None:
            active_section = inline_section
        expected_collections = enforced_sections.get(active_section or "")
        if expected_collections is None:
            continue
        if len(text) < minimum_characters or any(
            pattern.fullmatch(text) is not None for pattern in ignored_patterns
        ):
            continue
        if not any(pattern.fullmatch(text) is not None for pattern in required_patterns):
            continue
        requirements.append(
            {
                "source_id": str(block.get("source_id", "")),
                "section": str(active_section),
                "expected_collections": list(expected_collections),
            }
        )
    return requirements


def collect_source_section_coverage_violations(
    result: CVExtractionResult | dict[str, Any],
    source_blocks: list[dict[str, Any]],
    normalization_map: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    rules = source_coverage_rules()
    heading_patterns = {
        section: tuple(re.compile(pattern) for pattern in patterns)
        for section, patterns in rules["heading_patterns"].items()
    }
    inline_transition_patterns = {
        section: tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns)
        for section, patterns in rules["inline_section_transition_patterns"].items()
    }
    explicit_personal_name_patterns = tuple(
        re.compile(pattern) for pattern in rules["explicit_personal_name_patterns"]
    )
    personal_name_evidence_patterns = tuple(
        re.compile(pattern) for pattern in rules["personal_name_evidence_patterns"]
    )
    personal_header_name_patterns = tuple(
        re.compile(pattern) for pattern in rules["personal_header_name_patterns"]
    )
    personal_header_max_source_index = int(rules["personal_header_max_source_index"])
    explicit_language_patterns = tuple(
        re.compile(pattern) for pattern in rules["explicit_language_patterns"]
    )
    language_aliases = {
        canonical: frozenset(str(alias).casefold() for alias in aliases)
        for canonical, aliases in rules["language_aliases"].items()
    }
    enforced_sections = {
        section: frozenset(collections)
        for section, collections in rules["enforced_sections"].items()
    }
    ignored_patterns = tuple(
        re.compile(pattern) for pattern in rules["ignored_content_patterns"]
    )
    required_patterns = tuple(
        re.compile(pattern) for pattern in rules["required_content_patterns"]
    )
    minimum_characters = int(rules["minimum_content_characters"])
    owner_max_distance = int(rules["local_repair_owner_max_distance"])
    candidate_owner_max_distance = int(
        rules["local_repair_candidate_owner_max_distance"]
    )
    append_context_radius = int(rules["local_repair_append_context_radius"])
    append_trigger_patterns = tuple(
        re.compile(pattern) for pattern in rules["local_repair_append_trigger_patterns"]
    )
    preferred_collections = rules["local_repair_preferred_collections"]
    payload = (
        result.model_dump(exclude_none=True)
        if isinstance(result, CVExtractionResult)
        else result
    )
    collection_sources = {
        collection: _evidence_source_ids(payload.get(collection, []))
        for collection in CVExtractionResult.model_fields
    }
    violations: list[dict[str, Any]] = []
    personal_info = payload.get("personal_info")
    if not (
        isinstance(personal_info, dict)
        and isinstance(personal_info.get("name"), str)
        and personal_info["name"].strip()
    ):
        personal_evidence = (
            personal_info.get("evidence")
            if isinstance(personal_info, dict)
            else None
        )
        evidence_quote = (
            str(personal_evidence.get("quote", "")).strip()
            if isinstance(personal_evidence, dict)
            else ""
        )
        if evidence_quote and any(
            pattern.fullmatch(evidence_quote)
            for pattern in personal_name_evidence_patterns
        ):
            violations.append(
                {
                    "code": "explicit_personal_name_uncovered",
                    "entry_id": "personal_info",
                    "source_id": str(personal_evidence.get("source_id", "")),
                    "source_text": evidence_quote,
                }
            )
        else:
            name_source: tuple[str, str] | None = None
            for block in source_blocks:
                text = str(block.get("text", "")).strip()
                if any(
                    pattern.fullmatch(text)
                    for pattern in explicit_personal_name_patterns
                ):
                    name_source = (str(block.get("source_id", "")), text)
                    break
            if name_source is None:
                for index, block in enumerate(source_blocks):
                    if index > personal_header_max_source_index:
                        break
                    text = str(block.get("text", "")).strip()
                    if any(
                        pattern.fullmatch(text)
                        for pattern in personal_header_name_patterns
                    ):
                        name_source = (str(block.get("source_id", "")), text)
                        break
            if name_source is not None:
                violations.append(
                    {
                        "code": "explicit_personal_name_uncovered",
                        "entry_id": "personal_info",
                        "source_id": name_source[0],
                        "source_text": name_source[1],
                    }
                )
    covered_languages = {
        canonical
        for item in payload.get("languages", [])
        if isinstance(item, dict) and isinstance(item.get("language"), str)
        for canonical, aliases in language_aliases.items()
        if item["language"].strip().casefold() in aliases
    }
    uncovered_languages: set[str] = set()
    for block in source_blocks:
        source_id = str(block.get("source_id", ""))
        text = str(block.get("text", "")).strip()
        match = next(
            (
                candidate
                for pattern in explicit_language_patterns
                for candidate in [pattern.fullmatch(text)]
                if candidate is not None
            ),
            None,
        )
        if match is None:
            continue
        alias = match.group("language").casefold()
        canonical = next(
            (name for name, aliases in language_aliases.items() if alias in aliases),
            None,
        )
        if (
            canonical is None
            or canonical in covered_languages
            or canonical in uncovered_languages
        ):
            continue
        uncovered_languages.add(canonical)
        violations.append(
            {
                "code": "explicit_language_uncovered",
                "language": canonical,
                "source_id": source_id,
                "source_text": text,
                "suggested_append_collection": "languages",
            }
        )
    source_order = {
        str(block.get("source_id", "")): index
        for index, block in enumerate(source_blocks)
    }
    heading_source_ids = {
        str(block.get("source_id", ""))
        for block in source_blocks
        if any(
            pattern.fullmatch(str(block.get("text", "")).strip().rstrip(":：").strip())
            for patterns in heading_patterns.values()
            for pattern in patterns
        )
    }

    def infer_owner(
        source_id: str,
        expected_collections: frozenset[str],
        active_section: str | None,
    ) -> tuple[tuple[str, str] | None, list[dict[str, str]]]:
        missing_index = source_order.get(source_id)
        if missing_index is None:
            return None, []
        candidates: list[tuple[int, str, str]] = []
        for collection in sorted(expected_collections):
            entries = payload.get(collection, [])
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("entry_id"), str):
                    continue
                indices = sorted(
                    source_order[item]
                    for item in _evidence_source_ids(entry)
                    if item in source_order and item not in heading_source_ids
                )
                if not indices:
                    continue
                distance = (
                    indices[0] - missing_index
                    if missing_index < indices[0]
                    else missing_index - indices[-1]
                    if missing_index > indices[-1]
                    else 0
                )
                candidates.append((distance, collection, entry["entry_id"]))
        if not candidates:
            return None, []
        minimum = min(item[0] for item in candidates)
        nearest_items = [item for item in candidates if item[0] == minimum]
        preferred = preferred_collections.get(active_section or "")
        preferred_items = [item for item in nearest_items if item[1] == preferred]
        if preferred_items:
            nearest_items = preferred_items
        nearest = {(item[1], item[2]) for item in nearest_items}
        exact_owner = (
            next(iter(nearest))
            if minimum <= owner_max_distance and len(nearest) == 1
            else None
        )
        candidate_owners = [
            {"collection": collection, "entry_id": entry_id}
            for distance, collection, entry_id in sorted(candidates)
            if distance <= candidate_owner_max_distance
        ]
        return exact_owner, candidate_owners
    active_section: str | None = None
    for block in source_blocks:
        text = str(block.get("text", "")).strip()
        heading_text = text.rstrip(":：").strip()
        matched_section = next(
            (
                section
                for section, patterns in heading_patterns.items()
                if any(pattern.fullmatch(heading_text) is not None for pattern in patterns)
            ),
            None,
        )
        if matched_section is not None:
            active_section = matched_section
            continue
        inline_section = next(
            (
                section
                for section, patterns in inline_transition_patterns.items()
                if any(pattern.fullmatch(text) is not None for pattern in patterns)
            ),
            None,
        )
        if inline_section is not None:
            active_section = inline_section
        expected_collections = enforced_sections.get(active_section or "")
        if expected_collections is None:
            continue
        if len(text) < minimum_characters or any(
            pattern.fullmatch(text) is not None for pattern in ignored_patterns
        ):
            continue
        if not any(pattern.fullmatch(text) is not None for pattern in required_patterns):
            continue
        source_id = str(block.get("source_id", ""))
        actual_collections = sorted(
            collection
            for collection, source_ids in collection_sources.items()
            if source_id in source_ids
        )
        if expected_collections.intersection(actual_collections):
            continue
        violation = {
                "code": (
                    "source_section_misclassified"
                    if actual_collections
                    else "source_section_uncovered"
                ),
                "section": active_section,
                "source_id": source_id,
                "source_text": text,
                "expected_collections": sorted(expected_collections),
                "actual_collections": actual_collections,
            }
        if normalization_map is not None:
            taxonomy_skills = _matched_taxonomy_aliases(
                text,
                _taxonomy_source_aliases(normalization_map, rules),
            )
            if taxonomy_skills:
                violation["required_taxonomy_skills"] = [
                    {
                        "name": alias,
                        "skill_id": mapping["skill_id"],
                        "expected_item_type": mapping["category_code"],
                    }
                    for alias, mapping in taxonomy_skills.values()
                ]
        owner, candidate_owners = infer_owner(
            source_id, expected_collections, active_section
        )
        if owner is not None:
            violation["inferred_collection"] = owner[0]
            violation["entry_id"] = owner[1]
        else:
            preferred = preferred_collections.get(active_section or "")
            source_index = source_order.get(source_id)
            if (
                preferred in expected_collections
                and source_index is not None
                and any(pattern.fullmatch(text) for pattern in append_trigger_patterns)
            ):
                start = max(0, source_index - append_context_radius)
                end = min(len(source_blocks), source_index + append_context_radius + 1)
                violation["suggested_append_collection"] = preferred
                violation["context_source_ids"] = [
                    str(block.get("source_id", ""))
                    for block in source_blocks[start:end]
                ]
            elif candidate_owners and source_index is not None:
                violation["candidate_owners"] = candidate_owners
                start = max(0, source_index - append_context_radius)
                end = min(len(source_blocks), source_index + append_context_radius + 1)
                violation["context_source_ids"] = [
                    str(context_block.get("source_id", ""))
                    for context_block in source_blocks[start:end]
                ]
        violations.append(violation)
    return violations


def validate_source_section_coverage(
    result: CVExtractionResult | dict[str, Any],
    source_blocks: list[dict[str, Any]],
) -> None:
    violations = collect_source_section_coverage_violations(result, source_blocks)
    if violations:
        raise SemanticValidationError(
            "Source section coverage validation failed: "
            + json.dumps(violations, ensure_ascii=False, separators=(",", ":")),
            violations=violations,
        )


def collect_raw_match_field_evidence_violations(
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """Expose field/evidence inconsistencies even when another field blocks Schema validation."""
    specs = (
        ("personal_info", ("current_location", "expected_location", "expected_position", "expected_salary", "work_status", "available_date")),
        ("education", ("school", "college", "major", "degree", "date", "gpa", "gpa_scale", "location", "school_tag")),
        ("work_experience", ("company", "position", "date", "department", "location", "work_type")),
        ("project_experience", ("name", "date", "role", "affiliation")),
    )
    violations: list[dict[str, Any]] = []
    derived_fields = {"degree", "date", "work_type", "work_status"}
    for object_type, field_names in specs:
        raw_objects = payload.get(object_type)
        objects = [raw_objects] if object_type == "personal_info" else raw_objects
        if not isinstance(objects, list):
            continue
        for entry in objects:
            if not isinstance(entry, dict):
                continue
            object_id = "personal_info" if object_type == "personal_info" else entry.get("entry_id")
            if not isinstance(object_id, str):
                continue
            expected = {
                name
                for name in field_names
                if entry.get(name) is not None
                and not (name in {"degree", "work_type", "work_status"} and entry.get(name) == "unknown")
            }
            bindings = entry.get("field_evidence", [])
            if not isinstance(bindings, list):
                continue
            actual = [item.get("field_name") for item in bindings if isinstance(item, dict)]
            duplicates = sorted(name for name, count in Counter(actual).items() if count > 1 and isinstance(name, str))
            missing = sorted(expected.difference(actual))
            unexpected = sorted(set(actual).difference(expected))
            by_name = {
                item.get("field_name"): item
                for item in bindings
                if isinstance(item, dict) and isinstance(item.get("field_name"), str)
            }
            unsupported: list[str] = []
            for name in sorted(expected.difference(derived_fields)):
                value = entry.get(name)
                binding = by_name.get(name)
                evidence = binding.get("evidence") if isinstance(binding, dict) else None
                quote = evidence.get("quote") if isinstance(evidence, dict) else None
                if not isinstance(value, str) or not isinstance(quote, str):
                    continue
                normalized_value = "".join(unicodedata.normalize("NFKC", value).casefold().split())
                normalized_quote = "".join(unicodedata.normalize("NFKC", quote).casefold().split())
                if normalized_value not in normalized_quote:
                    unsupported.append(name)
            if missing or duplicates or unexpected or unsupported:
                violations.append(
                    {
                        "code": "invalid_match_field_evidence",
                        "entry_id": object_id,
                        "type": object_type,
                        "missing_fields": missing,
                        "duplicate_fields": duplicates,
                        "unexpected_fields": unexpected,
                        "unsupported_fields": unsupported,
                    }
                )
    return violations


def collect_raw_semantic_violations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Run object-local semantic checks without requiring the full Pydantic model.

    Schema-invalid candidates still need every independently detectable object error
    exposed in the same repair turn; otherwise bounded retries are consumed by a
    sequence of latent errors.
    """
    violations: list[dict[str, Any]] = []

    seen_projects: set[str] = set()
    projects = payload.get("project_experience", [])
    for index, entry in enumerate(projects if isinstance(projects, list) else []):
        if not isinstance(entry, dict):
            continue
        entry_id = entry.get("entry_id")
        name = entry.get("name")
        if not isinstance(entry_id, str) or not isinstance(name, str) or not name.strip():
            continue
        normalized_name = " ".join(unicodedata.normalize("NFKC", name).casefold().split())
        if normalized_name in seen_projects:
            violations.append(
                {
                    "code": "duplicate_entry_semantics",
                    "type": "project_experience",
                    "entry_id": entry_id,
                    "name": name,
                }
            )
        seen_projects.add(normalized_name)

        reasons = _project_name_shape_reasons(name)
        organization_role = _is_organization_role_project(name, entry.get("role"))
        if "role_title_instead_of_project_identifier" in reasons or organization_role:
            name_source_id = next(
                (
                    binding.get("evidence", {}).get("source_id")
                    for binding in entry.get("field_evidence", [])
                    if isinstance(binding, dict)
                    and binding.get("field_name") == "name"
                    and isinstance(binding.get("evidence"), dict)
                ),
                entry.get("evidence", {}).get("source_id")
                if isinstance(entry.get("evidence"), dict)
                else None,
            )
            violation = {
                "code": "role_title_as_project",
                "entry_id": entry_id,
                "name": name,
                "expected_collection": "work_experience",
            }
            if isinstance(name_source_id, str):
                violation["source_id"] = name_source_id
            violations.append(violation)
            if "role_title_instead_of_project_identifier" in reasons:
                reasons.remove("role_title_instead_of_project_identifier")
        if "activity_title_instead_of_project_identifier" in reasons:
            violations.append(
                {
                    "code": "activity_title_as_project",
                    "entry_id": entry_id,
                    "name": name,
                }
            )
            reasons.remove("activity_title_instead_of_project_identifier")
        if reasons:
            violations.append(
                {
                    "code": "invalid_project_name_shape",
                    "entry_id": entry_id,
                    "name": name,
                    "reasons": reasons,
                }
            )

    seen_awards: dict[tuple[str, str | None], str] = {}
    awards = payload.get("awards", [])
    for entry in awards if isinstance(awards, list) else []:
        if not isinstance(entry, dict):
            continue
        entry_id = entry.get("entry_id")
        name = entry.get("name")
        if not isinstance(entry_id, str) or not isinstance(name, str):
            continue
        if any(
            pattern.fullmatch(name.strip())
            for pattern in _AWARD_PROJECT_METRIC_PATTERNS
        ):
            violations.append(
                {
                    "code": "project_metric_as_award",
                    "type": "awards",
                    "entry_id": entry_id,
                    "name": name,
                }
            )
        key = _award_semantic_key(name)
        if key[0] and key in seen_awards:
            violations.append(
                {
                    "code": "duplicate_entry_semantics",
                    "type": "awards",
                    "entry_id": entry_id,
                    "duplicate_of_entry_id": seen_awards[key],
                    "name": name,
                }
            )
        else:
            seen_awards[key] = entry_id

    scoped_items: list[dict[str, Any]] = []
    skills = payload.get("skills", [])
    if isinstance(skills, list):
        scoped_items.extend(item for item in skills if isinstance(item, dict))
    for collection in ("work_experience", "project_experience"):
        entries = payload.get(collection, [])
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and isinstance(entry.get("tech_stack"), list):
                scoped_items.extend(
                    item for item in entry["tech_stack"] if isinstance(item, dict)
                )
    for index, item in enumerate(scoped_items):
        item_id = item.get("item_id")
        name = item.get("name")
        item_type = item.get("item_type")
        if all(isinstance(value, str) for value in (item_id, name, item_type)):
            violations.extend(
                _skill_shape_violations(
                    item_id=item_id,
                    name=name,
                    item_type=item_type,
                    skill_index=index,
                )
            )
    return violations


def _check_required_field_evidence(
    violations: list[dict[str, Any]],
    *,
    object_id: str,
    object_type: str,
    values: dict[str, Any],
    bindings: list[Any],
) -> None:
    expected = {
        field_name
        for field_name, value in values.items()
        if value is not None
        and not (field_name in {"degree", "work_type", "work_status"} and value == "unknown")
    }
    actual = [binding.field_name for binding in bindings]
    duplicates = sorted(name for name, count in Counter(actual).items() if count > 1)
    missing = sorted(expected.difference(actual))
    unexpected = sorted(set(actual).difference(expected))
    derived_fields = {"degree", "date", "work_type", "work_status"}
    binding_by_name = {binding.field_name: binding for binding in bindings}
    unsupported: list[str] = []
    for field_name in sorted(expected.difference(derived_fields)):
        binding = binding_by_name.get(field_name)
        value = values[field_name]
        if binding is None or not isinstance(value, str):
            continue
        normalized_value = "".join(
            unicodedata.normalize("NFKC", value).casefold().split()
        )
        normalized_quote = "".join(
            unicodedata.normalize("NFKC", binding.evidence.quote).casefold().split()
        )
        if normalized_value not in normalized_quote:
            unsupported.append(field_name)
    if missing or duplicates or unexpected or unsupported:
        violations.append(
            {
                "code": "invalid_match_field_evidence",
                "entry_id": object_id,
                "type": object_type,
                "missing_fields": missing,
                "duplicate_fields": duplicates,
                "unexpected_fields": unexpected,
                "unsupported_fields": unsupported,
            }
        )


def _check_match_field_evidence(
    violations: list[dict[str, Any]], result: CVExtractionResult
) -> None:
    personal = result.personal_info
    if personal is not None:
        _check_required_field_evidence(
            violations,
            object_id="personal_info",
            object_type="personal_info",
            values={
                name: getattr(personal, name)
                for name in (
                    "current_location", "expected_location", "expected_position",
                    "expected_salary", "work_status", "available_date",
                )
            },
            bindings=personal.field_evidence,
        )
    for entry in result.education:
        _check_required_field_evidence(
            violations,
            object_id=entry.entry_id,
            object_type="education",
            values={
                "school": entry.school, "college": entry.college, "major": entry.major,
                "degree": entry.degree, "date": entry.date, "gpa": entry.gpa,
                "gpa_scale": entry.gpa_scale, "location": entry.location,
                "school_tag": entry.school_tag,
            },
            bindings=entry.field_evidence,
        )
    for entry in result.work_experience:
        _check_required_field_evidence(
            violations,
            object_id=entry.entry_id,
            object_type="work_experience",
            values={
                "company": entry.company, "position": entry.position, "date": entry.date,
                "department": entry.department, "location": entry.location,
                "work_type": entry.work_type,
            },
            bindings=entry.field_evidence,
        )
    for entry in result.project_experience:
        _check_required_field_evidence(
            violations,
            object_id=entry.entry_id,
            object_type="project_experience",
            values={
                "name": entry.name, "date": entry.date, "role": entry.role,
                "affiliation": entry.affiliation,
            },
            bindings=entry.field_evidence,
        )


def validate_skill_item_type_contract(
    result: CVExtractionResult,
    normalization_map: dict[str, Any],
) -> None:
    violations: list[dict[str, Any]] = []
    for item in _iter_skills(result):
        if lookup_skill_mapping(normalization_map, item.name, item.item_type) is not None:
            continue
        mappings = skill_mapping_candidates(normalization_map, item.name)
        if not mappings:
            composite_parts = split_taxonomy_confirmed_shared_skill_name(
                item.name,
                lambda candidate: len(
                    skill_mapping_candidates(normalization_map, candidate)
                ) == 1,
            )
            if composite_parts is not None:
                violations.append(
                    {
                        "code": "composite_skill_item",
                        "item_id": item.item_id,
                        "name": item.name,
                        "parts": composite_parts,
                    }
                )
                continue
        expected_types = sorted(
            {
                mapping.get("category_code")
                for mapping in mappings
                if isinstance(mapping, dict)
                and isinstance(mapping.get("category_code"), str)
            }
        )
        if not expected_types:
            continue
        violation: dict[str, Any] = {
            "code": "skill_item_type_mismatch",
            "item_id": item.item_id,
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


def _taxonomy_alias_spans(
    text: str, alias: str, *, exact_case: bool = False
) -> list[tuple[int, int]]:
    case_preserved_text = unicodedata.normalize("NFKC", text)
    case_preserved_alias = unicodedata.normalize("NFKC", alias)
    normalized_text = case_preserved_text.casefold()
    normalized_alias = case_preserved_alias.casefold()
    ascii_letters = [character for character in case_preserved_alias if character.isascii() and character.isalpha()]
    requires_exact_case = exact_case or (
        1 < len(ascii_letters) <= 3
        and all(character.isupper() for character in ascii_letters)
    )
    start = normalized_text.find(normalized_alias)
    spans: list[tuple[int, int]] = []

    def is_ascii_alphanumeric(character: str) -> bool:
        return character.isascii() and character.isalnum()

    def is_left_identifier_character(index: int) -> bool:
        character = normalized_text[index]
        return is_ascii_alphanumeric(character) or (
            character in "._-"
            and index > 0
            and is_ascii_alphanumeric(normalized_text[index - 1])
        )

    def is_right_identifier_character(index: int) -> bool:
        character = normalized_text[index]
        return is_ascii_alphanumeric(character) or (
            character in "._-"
            and index + 1 < len(normalized_text)
            and is_ascii_alphanumeric(normalized_text[index + 1])
        )

    while start >= 0:
        end = start + len(normalized_alias)
        left_ok = (
            start == 0
            or not normalized_alias[0].isascii()
            or not normalized_alias[0].isalnum()
            or not is_left_identifier_character(start - 1)
        )
        right_ok = (
            end == len(normalized_text)
            or not normalized_alias[-1].isascii()
            or not normalized_alias[-1].isalnum()
            or not is_right_identifier_character(end)
        )
        exact_case_ok = (
            not requires_exact_case
            or case_preserved_text[start:end] == case_preserved_alias
        )
        if left_ok and right_ok and exact_case_ok:
            spans.append((start, end))
        start = normalized_text.find(normalized_alias, start + 1)
    return spans


def _taxonomy_alias_occurs(text: str, alias: str) -> bool:
    return bool(_taxonomy_alias_spans(text, alias))


def _taxonomy_source_aliases(
    normalization_map: dict[str, Any],
    rules: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    minimum_characters = int(rules["taxonomy_skill_minimum_alias_characters"])
    ignored_alias_patterns = tuple(
        re.compile(pattern) for pattern in rules["taxonomy_skill_ignored_alias_patterns"]
    )
    default_categories = frozenset(rules["taxonomy_skill_default_categories"])
    casefold_skill_ids: dict[str, set[str]] = {}
    for alias, mapping in normalization_map.get("skills", {}).items():
        if isinstance(alias, str) and isinstance(mapping, dict):
            skill_id = mapping.get("skill_id")
            if isinstance(skill_id, str):
                folded = unicodedata.normalize("NFKC", alias).casefold()
                casefold_skill_ids.setdefault(folded, set()).add(skill_id)
    aliases: list[tuple[str, dict[str, Any]]] = []
    for alias, mapping in normalization_map.get("skills", {}).items():
        compact = "".join(unicodedata.normalize("NFKC", str(alias)).split())
        source_coverage = mapping.get("source_coverage") if isinstance(mapping, dict) else None
        if (
            isinstance(alias, str)
            and isinstance(mapping, dict)
            and len(compact) >= minimum_characters
            and not any(pattern.fullmatch(alias) for pattern in ignored_alias_patterns)
            and source_coverage is not False
            and (
                source_coverage is True
                or (
                    mapping.get("category_code") in default_categories
                    and any(character.isascii() and character.isalpha() for character in alias)
                )
            )
        ):
            source_mapping = dict(mapping)
            folded = unicodedata.normalize("NFKC", alias).casefold()
            if len(casefold_skill_ids.get(folded, set())) > 1:
                source_mapping["_source_exact_case"] = True
            aliases.append((alias, source_mapping))
    aliases.sort(key=lambda item: (-len(item[0]), item[0]))
    return aliases


def _matched_taxonomy_aliases(
    text: str,
    aliases: list[tuple[str, dict[str, Any]]],
) -> dict[str, tuple[str, dict[str, Any]]]:
    matched_by_skill_id: dict[str, tuple[str, dict[str, Any]]] = {}
    occupied_spans: list[tuple[int, int]] = []
    for alias, mapping in aliases:
        skill_id = mapping.get("skill_id")
        if not isinstance(skill_id, str) or skill_id in matched_by_skill_id:
            continue
        available_spans = [
            span
            for span in _taxonomy_alias_spans(
                text,
                alias,
                exact_case=mapping.get("_source_exact_case") is True,
            )
            if not any(
                span[0] < occupied[1] and occupied[0] < span[1]
                for occupied in occupied_spans
            )
        ]
        if available_spans:
            matched_by_skill_id[skill_id] = (alias, mapping)
            occupied_spans.extend(available_spans)
    return matched_by_skill_id


def _taxonomy_source_text_is_ignored(text: str, rules: dict[str, Any]) -> bool:
    return any(
        re.compile(pattern).search(text) is not None
        for pattern in rules["taxonomy_skill_ignored_source_patterns"]
    )


def _slash_shorthand_supports(name: str, text: str) -> bool:
    normalized_name = unicodedata.normalize("NFKC", name)
    normalized_text = unicodedata.normalize("NFKC", text)
    if re.search(
        rf"{re.escape(normalized_name[:-1])}\d+\s*/\s*{re.escape(normalized_name[-1:])}(?!\d)",
        normalized_text,
    ) is not None and normalized_name[-1:].isdigit():
        return True
    for slash in re.finditer(r"[/／]", normalized_text):
        left_match = re.search(
            r"([A-Za-z0-9.+#\-\u4e00-\u9fff]+)\s*$",
            normalized_text[: slash.start()],
        )
        if left_match is None:
            continue
        right = re.split(
            r"[,，、;；。:：()（）]",
            normalized_text[slash.end() :],
            maxsplit=1,
        )[0].strip()
        left = left_match.group(1)
        if not normalized_name.startswith(left):
            continue
        suffix = normalized_name[len(left) :]
        if suffix and right.endswith(suffix):
            return True
    return False


def _skill_name_supported_by_source(
    item: SkillItem,
    source_text: str,
    normalization_map: dict[str, Any],
    aliases_by_skill_id: dict[str, list[str]],
    exact_case_aliases: set[str],
) -> bool:
    mapping = lookup_skill_mapping(normalization_map, item.name, item.item_type)
    if mapping is None:
        candidates = skill_mapping_candidates(normalization_map, item.name)
        mapping = candidates[0] if len(candidates) == 1 else None
    if isinstance(mapping, dict) and isinstance(mapping.get("skill_id"), str):
        aliases = aliases_by_skill_id.get(mapping["skill_id"], [item.name])
        if any(
            _taxonomy_alias_spans(
                source_text,
                alias,
                exact_case=alias in exact_case_aliases,
            )
            for alias in aliases
        ):
            return True
    elif item.name in unicodedata.normalize("NFKC", source_text):
        return True
    return _slash_shorthand_supports(item.name, source_text)


def collect_skill_evidence_support_violations(
    result: CVExtractionResult,
    normalization_map: dict[str, Any],
    source_blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Require every skill Evidence source to explicitly support that skill identity."""
    blocks = {
        str(block["source_id"]): str(block["text"])
        for block in source_blocks
    }
    aliases_by_skill_id: dict[str, list[str]] = {}
    casefold_skill_ids: dict[str, set[str]] = {}
    for alias, mapping in normalization_map.get("skills", {}).items():
        if not isinstance(alias, str) or not isinstance(mapping, dict):
            continue
        skill_id = mapping.get("skill_id")
        if isinstance(skill_id, str):
            aliases_by_skill_id.setdefault(skill_id, []).append(alias)
            folded = unicodedata.normalize("NFKC", alias).casefold()
            casefold_skill_ids.setdefault(folded, set()).add(skill_id)
    exact_case_aliases = {
        alias
        for aliases in aliases_by_skill_id.values()
        for alias in aliases
        if len(
            casefold_skill_ids.get(
                unicodedata.normalize("NFKC", alias).casefold(), set()
            )
        )
        > 1
    }

    scoped_items: list[tuple[SkillItem, set[str]]] = [
        (item, set()) for item in result.skills
    ]
    for entry in [*result.work_experience, *result.project_experience]:
        owner_source_ids = _evidence_source_ids(entry.model_dump(mode="json"))
        scoped_items.extend((item, owner_source_ids) for item in entry.tech_stack)

    violations: list[dict[str, Any]] = []
    for item, owner_source_ids in scoped_items:
        source_id = item.evidence.source_id
        source_text = blocks.get(source_id)
        if source_text is None or _skill_name_supported_by_source(
            item,
            source_text,
            normalization_map,
            aliases_by_skill_id,
            exact_case_aliases,
        ):
            continue
        candidate_source_ids = sorted(
            candidate_source_id
            for candidate_source_id in owner_source_ids
            if candidate_source_id != source_id
            and candidate_source_id in blocks
            and _skill_name_supported_by_source(
                item,
                blocks[candidate_source_id],
                normalization_map,
                aliases_by_skill_id,
                exact_case_aliases,
            )
        )
        violation: dict[str, Any] = {
            "code": "skill_evidence_name_uncovered",
            "item_id": item.item_id,
            "name": item.name,
            "source_id": source_id,
            "source_text": source_text,
        }
        if candidate_source_ids:
            violation["candidate_source_ids"] = candidate_source_ids
        violations.append(violation)
    return violations


def collect_source_taxonomy_requirements(
    normalization_map: dict[str, Any],
    source_blocks: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Expose only source-local exact taxonomy terms that the final gate already requires."""
    rules = source_coverage_rules()
    heading_patterns = {
        section: tuple(re.compile(pattern) for pattern in patterns)
        for section, patterns in rules["heading_patterns"].items()
    }
    inline_transition_patterns = {
        section: tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns)
        for section, patterns in rules["inline_section_transition_patterns"].items()
    }
    checked_sections = frozenset(rules["taxonomy_skill_checked_sections"])
    aliases = _taxonomy_source_aliases(normalization_map, rules)
    requirements: list[dict[str, str]] = []
    active_section: str | None = None
    for block in source_blocks:
        text = str(block.get("text", "")).strip()
        heading_text = text.rstrip(":：").strip()
        matched_section = next(
            (
                section
                for section, patterns in heading_patterns.items()
                if any(pattern.fullmatch(heading_text) is not None for pattern in patterns)
            ),
            None,
        )
        if matched_section is not None:
            active_section = matched_section
            continue
        inline_section = next(
            (
                section
                for section, patterns in inline_transition_patterns.items()
                if any(pattern.fullmatch(text) is not None for pattern in patterns)
            ),
            None,
        )
        if inline_section is not None:
            active_section = inline_section
        if active_section not in checked_sections:
            continue
        if _taxonomy_source_text_is_ignored(text, rules):
            continue
        source_id = str(block.get("source_id", ""))
        for alias, mapping in _matched_taxonomy_aliases(text, aliases).values():
            requirements.append(
                {
                    "source_id": source_id,
                    "section": active_section,
                    "name": alias,
                    "item_type": str(mapping["category_code"]),
                }
            )
    return requirements


def collect_taxonomy_skill_coverage_violations(
    result: CVExtractionResult | dict[str, Any],
    normalization_map: dict[str, Any],
    source_blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Find explicit JD-taxonomy skills omitted from the corresponding CV scope."""
    rules = source_coverage_rules()
    heading_patterns = {
        section: tuple(re.compile(pattern) for pattern in patterns)
        for section, patterns in rules["heading_patterns"].items()
    }
    inline_transition_patterns = {
        section: tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns)
        for section, patterns in rules["inline_section_transition_patterns"].items()
    }
    checked_sections = frozenset(rules["taxonomy_skill_checked_sections"])
    aliases = _taxonomy_source_aliases(normalization_map, rules)

    payload = (
        result.model_dump(exclude_none=True)
        if isinstance(result, CVExtractionResult)
        else result
    )

    def mapped_skill_ids(items: Iterator[Any]) -> set[str]:
        found: set[str] = set()
        for item in items:
            if isinstance(item, SkillItem):
                name, item_type = item.name, item.item_type
            elif isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(
                item.get("item_type"), str
            ):
                name, item_type = item["name"], item["item_type"]
            else:
                continue
            mapping = lookup_skill_mapping(normalization_map, name, item_type)
            if mapping is None:
                candidates = skill_mapping_candidates(normalization_map, name)
                mapping = candidates[0] if len(candidates) == 1 else None
            if isinstance(mapping, dict) and isinstance(mapping.get("skill_id"), str):
                found.add(mapping["skill_id"])
        return found

    declared_skills = payload.get("skills", [])
    declared_ids = mapped_skill_ids(
        iter(declared_skills if isinstance(declared_skills, list) else [])
    )
    scoped_entries: dict[str, list[tuple[str, str, set[str], set[str]]]] = {
        "work": [],
        "project": [],
    }
    for section, collection, entries in (
        ("work", "work_experience", payload.get("work_experience", [])),
        ("project", "project_experience", payload.get("project_experience", [])),
    ):
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict) or not isinstance(entry.get("entry_id"), str):
                continue
            tech_stack = entry.get("tech_stack", [])
            scoped_entries[section].append(
                (
                    collection,
                    entry["entry_id"],
                    _evidence_source_ids(entry),
                    mapped_skill_ids(
                        iter(tech_stack if isinstance(tech_stack, list) else [])
                    ),
                )
            )

    source_order = {
        str(block.get("source_id", "")): index
        for index, block in enumerate(source_blocks)
    }
    owner_contexts: dict[tuple[str, str], tuple[str, ...]] = {}
    for entries in scoped_entries.values():
        for collection, entry_id, source_ids, _ in entries:
            indices = sorted(
                source_order[source_id]
                for source_id in source_ids
                if source_id in source_order
            )
            if not indices:
                continue
            owner_contexts[(collection, entry_id)] = tuple(
                str(block.get("source_id", ""))
                for block in source_blocks[indices[0] : indices[-1] + 1]
            )

    def evidence_owners(
        source_id: str,
        active_section: str | None,
    ) -> list[tuple[str, str | None, set[str]]]:
        if active_section in scoped_entries:
            return [
                (collection, entry_id, extracted_ids)
                for collection, entry_id, source_ids, extracted_ids in scoped_entries[
                    active_section
                ]
                if source_id in source_ids
            ]
        missing_index = source_order.get(source_id)
        if missing_index is None:
            return []
        exact_candidates: list[tuple[str, str, set[str]]] = []
        for collection, entry_id, source_ids, extracted_ids in scoped_entries["project"]:
            known_source_ids = {item for item in source_ids if item in source_order}
            if len(known_source_ids) >= 2 and source_id in known_source_ids:
                exact_candidates.append((collection, entry_id, extracted_ids))
        exact_identities = {(item[0], item[1]) for item in exact_candidates}
        if len(exact_identities) == 1:
            return exact_candidates[:1]
        if exact_candidates:
            return []
        candidates: list[tuple[str, str, set[str]]] = []
        for section in ("work", "project"):
            for collection, entry_id, source_ids, extracted_ids in scoped_entries[section]:
                indices = sorted(
                    source_order[item]
                    for item in source_ids
                    if item in source_order
                )
                if len(indices) < 2 or source_id in source_ids:
                    continue
                if indices[0] < missing_index < indices[-1]:
                    candidates.append((collection, entry_id, extracted_ids))
        identities = {(item[0], item[1]) for item in candidates}
        if len(identities) != 1:
            return []
        return candidates[:1]

    violations: list[dict[str, Any]] = []
    active_section: str | None = None
    for block in source_blocks:
        text = str(block.get("text", "")).strip()
        heading_text = text.rstrip(":：").strip()
        matched_section = next(
            (
                section
                for section, patterns in heading_patterns.items()
                if any(pattern.fullmatch(heading_text) is not None for pattern in patterns)
            ),
            None,
        )
        if matched_section is not None:
            active_section = matched_section
            continue
        inline_section = next(
            (
                section
                for section, patterns in inline_transition_patterns.items()
                if any(pattern.fullmatch(text) is not None for pattern in patterns)
            ),
            None,
        )
        if inline_section is not None:
            active_section = inline_section
        if _taxonomy_source_text_is_ignored(text, rules):
            continue
        source_id = str(block.get("source_id", ""))
        owners: list[tuple[str, str | None, set[str]]]
        bound_owners = evidence_owners(source_id, active_section)
        if bound_owners:
            # OCR headings can be missing or malformed, while the extracted
            # object still bounds this source block inside its Evidence span.
            # Prefer that authoritative ownership so an empty tech_stack cannot
            # bypass coverage merely because active_section was not recognized.
            owners = bound_owners
        elif active_section not in checked_sections:
            continue
        elif active_section == "skills":
            owners = [("skills", None, declared_ids)]
        else:
            owners = [
                (collection, entry_id, extracted_ids)
                for collection, entry_id, source_ids, extracted_ids
                in scoped_entries.get(active_section, [])
                if source_id in source_ids
            ]
        if not owners:
            continue

        matched_by_skill_id = _matched_taxonomy_aliases(text, aliases)
        for collection, entry_id, extracted_ids in owners:
            for skill_id, (alias, mapping) in matched_by_skill_id.items():
                if skill_id in extracted_ids:
                    continue
                violation: dict[str, Any] = {
                    "code": "taxonomy_skill_uncovered",
                    "source_id": source_id,
                    "source_text": text,
                    "target_collection": collection,
                    "name": alias,
                    "skill_id": skill_id,
                    "expected_item_type": mapping["category_code"],
                }
                if entry_id is not None:
                    violation["entry_id"] = entry_id
                    context_source_ids = owner_contexts.get((collection, entry_id))
                    if context_source_ids:
                        violation["context_source_ids"] = list(context_source_ids)
                violations.append(violation)
    deduplicated: dict[tuple[str, str | None, str], dict[str, Any]] = {}
    for violation in violations:
        key = (
            str(violation["target_collection"]),
            violation.get("entry_id"),
            str(violation["skill_id"]),
        )
        deduplicated.setdefault(key, violation)
    output = list(deduplicated.values())
    project_entries = scoped_entries["project"]
    project_omission_detected = any(
        item.get("target_collection") == "project_experience"
        for item in output
    )
    if (
        project_entries
        and project_omission_detected
        and all(not extracted_ids for _, _, _, extracted_ids in project_entries)
    ):
        for collection, entry_id, _, _ in project_entries:
            context_source_ids = owner_contexts.get((collection, entry_id), ())
            output.append(
                {
                    "code": "project_tech_stack_catastrophic_omission",
                    "target_collection": collection,
                    "entry_id": entry_id,
                    "context_source_ids": list(context_source_ids),
                }
            )
    return output


def _check_duplicate_education(violations: list[dict], result: CVExtractionResult) -> None:
    seen: set[tuple[str, str, str]] = set()
    for entry in result.education:
        key = (entry.school.casefold(), entry.major.casefold(), str(entry.degree))
        if key in seen:
            violations.append(
                {
                    "code": "duplicate_entry_semantics",
                    "type": "education",
                    "entry_id": entry.entry_id,
                    "school": entry.school,
                    "major": entry.major,
                }
            )
        seen.add(key)


def _check_duplicate_work(violations: list[dict], result: CVExtractionResult) -> None:
    seen: set[tuple[str, str]] = set()
    for entry in result.work_experience:
        key = (
            " ".join(unicodedata.normalize("NFKC", entry.company).casefold().split()),
            " ".join(unicodedata.normalize("NFKC", entry.position or "").casefold().split()),
        )
        if key in seen:
            violations.append(
                {
                    "code": "duplicate_entry_semantics",
                    "type": "work_experience",
                    "entry_id": entry.entry_id,
                    "company": entry.company,
                    "position": entry.position,
                }
            )
        seen.add(key)


def _check_duplicate_projects(violations: list[dict], result: CVExtractionResult) -> None:
    seen: set[str] = set()
    for entry in result.project_experience:
        key = " ".join(unicodedata.normalize("NFKC", entry.name).casefold().split())
        if key in seen:
            violations.append(
                {
                    "code": "duplicate_entry_semantics",
                    "type": "project_experience",
                    "entry_id": entry.entry_id,
                    "name": entry.name,
                }
            )
        seen.add(key)


def _explicit_award_level(text: str) -> str | None:
    matches = [
        (match.start(), level)
        for level, pattern in _AWARD_LEVEL_PATTERNS.items()
        for match in pattern.finditer(text)
    ]
    if not matches:
        return None
    last_start = max(start for start, _ in matches)
    levels = {level for start, level in matches if start == last_start}
    return next(iter(levels)) if len(levels) == 1 else None


def _award_semantic_key(name: str) -> tuple[str, str | None]:
    normalized = unicodedata.normalize("NFKC", name).casefold()
    level = _explicit_award_level(normalized)
    normalized = re.sub(r"(?:19|20)\d{2}(?:[-./年]\d{1,2})?", "", normalized)
    for pattern in _AWARD_SEMANTIC_SCOPE_STRIP_PATTERNS:
        normalized = pattern.sub("", normalized)
    normalized = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalized)
    return normalized, level


def _check_duplicate_awards(violations: list[dict], result: CVExtractionResult) -> None:
    seen: dict[tuple[str, str | None], str] = {}
    for entry in result.awards:
        key = _award_semantic_key(entry.name)
        if key[0] and key in seen:
            violations.append(
                {
                    "code": "duplicate_entry_semantics",
                    "type": "awards",
                    "entry_id": entry.entry_id,
                    "duplicate_of_entry_id": seen[key],
                    "name": entry.name,
                }
            )
        else:
            seen[key] = entry.entry_id


def _check_personal_location_shape(
    violations: list[dict[str, Any]], result: CVExtractionResult
) -> None:
    personal = result.personal_info
    if personal is None:
        return
    for field_name in ("current_location", "expected_location"):
        value = getattr(personal, field_name)
        if not isinstance(value, str) or _LOCATION_INSTITUTION_SUFFIX_PATTERN.search(value) is None:
            continue
        violations.append(
            {
                "code": "invalid_personal_location_shape",
                "entry_id": "personal_info",
                "field_name": field_name,
                "value": value,
            }
        )


def _check_project_name_shape(violations: list[dict], result: CVExtractionResult) -> None:
    for entry in result.project_experience:
        reasons = _project_name_shape_reasons(entry.name)
        organization_role = _is_organization_role_project(entry.name, entry.role)
        if "role_title_instead_of_project_identifier" in reasons or organization_role:
            name_binding = next(
                binding
                for binding in entry.field_evidence
                if binding.field_name == "name"
            )
            violations.append(
                {
                    "code": "role_title_as_project",
                    "entry_id": entry.entry_id,
                    "name": entry.name,
                    "source_id": name_binding.evidence.source_id,
                    "expected_collection": "work_experience",
                }
            )
            if "role_title_instead_of_project_identifier" in reasons:
                reasons.remove("role_title_instead_of_project_identifier")
        if "activity_title_instead_of_project_identifier" in reasons:
            violations.append(
                {
                    "code": "activity_title_as_project",
                    "entry_id": entry.entry_id,
                    "name": entry.name,
                }
            )
            reasons.remove("activity_title_instead_of_project_identifier")
        if reasons:
            violations.append(
                {
                    "code": "invalid_project_name_shape",
                    "entry_id": entry.entry_id,
                    "name": entry.name,
                    "reasons": reasons,
                }
            )


def _check_competition_project_names(
    violations: list[dict], result: CVExtractionResult
) -> None:
    for entry in result.project_experience:
        if PROJECT_NAME_COMPETITION_PATTERN.fullmatch(entry.name.strip()) is None:
            continue
        sourced_facts = [*entry.highlights]
        if entry.description is not None:
            sourced_facts.append(entry.description)
        artifact_sources = sorted(
            {
                fact.evidence.source_id
                for fact in sourced_facts
                if PROJECT_TECHNICAL_ARTIFACT_PATTERN.fullmatch(fact.evidence.quote)
                is not None
            }
        )
        if artifact_sources:
            violations.append(
                {
                    "code": "competition_title_as_project_name",
                    "entry_id": entry.entry_id,
                    "name": entry.name,
                    "artifact_source_ids": artifact_sources,
                }
            )


def _flag(
    result: CVExtractionResult,
    issue_type: str,
    *,
    item_id: str | None = None,
) -> dict[str, Any]:
    rule = get_review_rule(issue_type)
    payload: dict[str, Any] = {
        "cv_id": result.document_id,
        "issue_type": issue_type,
        "severity": rule["severity"],
        "rule_scope": rule["scope"],
        "description": rule["description"],
        "suggested_action": rule["suggested_action"],
    }
    if item_id is not None:
        payload["item_id"] = item_id
    return payload


def validate_business_rules(result: CVExtractionResult) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    if result.personal_info is None:
        flags.append(_flag(result, "missing_personal_info"))
    elif not result.personal_info.name:
        flags.append(_flag(result, "missing_name"))
    if not result.education:
        flags.append(_flag(result, "missing_education"))
    if not result.work_experience and not result.project_experience:
        flags.append(_flag(result, "missing_experience"))

    scoped_skills = [("skills", result.skills)] + [
        (f"work_experience:{work.entry_id}:tech_stack", work.tech_stack)
        for work in result.work_experience
    ] + [
        (f"project_experience:{project.entry_id}:tech_stack", project.tech_stack)
        for project in result.project_experience
    ]
    for _, skills in scoped_skills:
        seen: set[tuple[str, str]] = set()
        for item in skills:
            key = (item.name.casefold(), item.item_type)
            if key in seen:
                flags.append(_flag(result, "duplicate_skill", item_id=item.item_id))
            seen.add(key)
    for item in result.skills:
        if item.proficiency in (None, "unknown"):
            flags.append(
                _flag(
                    result,
                    "unknown_skill_proficiency",
                    item_id=item.item_id,
                )
            )
    for item in _iter_skills(result):
        if item.item_type == "other":
            flags.append(
                _flag(
                    result,
                    "skill_item_other_requires_review",
                    item_id=item.item_id,
                )
            )
    return flags


def validate_normalized_rules(result: CVNormalizedResult) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    rule = get_review_rule("unresolved_skill_normalization")
    for skill in result.normalized_skills:
        if skill.resolution_status != "resolved":
            flags.append(
                {
                    "cv_id": result.document_id,
                    "issue_type": "unresolved_skill_normalization",
                    "severity": rule["severity"],
                    "rule_scope": rule["scope"],
                    "item_id": skill.source_item_id,
                    "description": rule["description"],
                    "suggested_action": rule["suggested_action"],
                }
            )
    return flags


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
                "cv_id": document_id,
                "document_index": row_index,
                "field_path": ".".join(str(part) for part in location),
                "field": str(location[-1]) if location else "",
                "raw_value": value,
                "allowed_values": re.findall(r"'([^']+)'", error.get("ctx", {}).get("expected", "")),
                "error_message": error.get("msg", ""),
            }
        )
    return cases
