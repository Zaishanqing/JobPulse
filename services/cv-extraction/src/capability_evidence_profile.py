"""Deterministic Capability Evidence Profile builder for confirmed CV snapshots."""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any, Iterable, Mapping

from .models import (
    CVCapabilityEvidenceProfileResult,
    CVExtractionResult,
    CVNormalizedResult,
    CapabilityEvidenceItem,
    CapabilityEvidenceProfile,
    Evidence,
    ProjectEntry,
    SkillItem,
    WorkEntry,
)


CAPABILITY_EVIDENCE_DERIVATION_VERSION = "1.0"
EVIDENCE_LEVEL_ORDER = {
    "declared_only": 0,
    "course_used": 1,
    "project_used": 2,
    "work_used": 3,
    "owned_component": 4,
    "designed_system": 5,
    "measured_result": 6,
}
OWNERSHIP_ORDER = {
    "unknown": 0,
    "participated": 1,
    "implemented": 2,
    "owned": 3,
    "designed": 4,
    "led": 5,
}
DEPTH_ORDER = {
    "unknown": 0,
    "declared": 1,
    "used": 2,
    "implemented": 3,
    "designed": 4,
    "led": 5,
}
RECENCY_ORDER = {"unknown": 0, "old": 1, "moderate": 2, "recent": 3}
PRESENT_TOKENS = {"至今", "现在", "目前", "今", "present", "current", "now"}
_DATE_PATTERN = re.compile(r"(?P<year>19\d{2}|20\d{2})(?:\s*[./\-年]\s*(?P<month>1[0-2]|0?[1-9]))?")
_COURSE_PATTERN = re.compile(r"课程|学习|培训|course|tutorial", re.IGNORECASE)
_MEASURABLE_PATTERN = re.compile(
    r"(?:提升|提高|降低|减少|达到|至|超过|准确率|精度|recall|f1|指标|score|性能|吞吐)"
    r".{0,24}?\d+(?:\.\d+)?%?",
    re.IGNORECASE,
)
_DESIGN_PATTERN = re.compile(r"设计|架构|pipeline|流程设计", re.IGNORECASE)
_LEAD_PATTERN = re.compile(r"主导|带领|lead|负责人|owner", re.IGNORECASE)
_OWNED_PATTERN = re.compile(r"独立|自主|单独|负责|own", re.IGNORECASE)
_IMPLEMENT_PATTERN = re.compile(r"实现|开发|编写|完成|落地|优化|构建|fine-?tun", re.IGNORECASE)
_PARTICIPATE_PATTERN = re.compile(r"参与|协助|支持|配合|participat|assist", re.IGNORECASE)


def _normalized(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _stable_id(*parts: str) -> str:
    cleaned = [re.sub(r"[^A-Za-z0-9_.-]", "_", part) for part in parts]
    return "_".join(part for part in cleaned if part)


def _evidence_key(evidence: Evidence) -> tuple[Any, ...]:
    return (
        evidence.source_id,
        evidence.start,
        evidence.end,
        evidence.occurrence_index,
        evidence.quote,
    )


def _unique_evidence(items: Iterable[Evidence]) -> list[Evidence]:
    found: dict[tuple[Any, ...], Evidence] = {}
    for evidence in items:
        found.setdefault(_evidence_key(evidence), evidence)
    return list(found.values())


def _month_index(value: str | None, as_of_date: date) -> int | None:
    if value is None:
        return None
    normalized = _normalized(value).strip(" .-/年月")
    if normalized in PRESENT_TOKENS:
        return as_of_date.year * 12 + as_of_date.month - 1
    match = _DATE_PATTERN.search(unicodedata.normalize("NFKC", value))
    if match is None:
        return None
    year = int(match.group("year"))
    month = int(match.group("month") or 1)
    return year * 12 + month - 1


def _recency(date_range: Any, as_of_date: date) -> str:
    end = date_range.end if date_range is not None else None
    start = date_range.start if date_range is not None else None
    end_index = _month_index(end, as_of_date)
    if end_index is None:
        end_index = _month_index(start, as_of_date)
    as_of_index = as_of_date.year * 12 + as_of_date.month - 1
    if end_index is None:
        return "unknown"
    months = max(0, as_of_index - end_index)
    if months <= 12:
        return "recent"
    if months <= 36:
        return "moderate"
    return "old"


def _skill_key(item: SkillItem, normalized_by_item: dict[str, Any]) -> tuple[str, ...]:
    normalized = normalized_by_item.get(item.item_id)
    if normalized is not None and normalized.skill_id:
        return ("canonical", normalized.skill_id, normalized.canonical_name or item.name)
    return ("raw", item.item_type, normalized.canonical_name if normalized else item.name)


def _skill_display(item: SkillItem, normalized_by_item: dict[str, Any]) -> tuple[str | None, str]:
    normalized = normalized_by_item.get(item.item_id)
    if normalized is not None:
        return normalized.skill_id, normalized.canonical_name or item.name
    return None, item.name


def _task_texts(entry: WorkEntry | ProjectEntry) -> list[tuple[str, Evidence]]:
    texts: list[tuple[str, Evidence]] = []
    if isinstance(entry, WorkEntry):
        for fact in [*entry.responsibilities, *entry.achievements]:
            texts.append((fact.value, fact.evidence))
    else:
        if entry.description is not None:
            texts.append((entry.description.value, entry.description.evidence))
        texts.extend((fact.value, fact.evidence) for fact in entry.highlights)
    return texts


def _is_ascii_word_char(character: str) -> bool:
    return character.isascii() and (character.isalnum() or character == "_")


def _skill_token_occurs(text: str, alias: str) -> bool:
    if not alias:
        return False
    start = 0
    while True:
        index = text.find(alias, start)
        if index < 0:
            return False
        end = index + len(alias)
        left_ok = (
            not _is_ascii_word_char(alias[0])
            or index == 0
            or not _is_ascii_word_char(text[index - 1])
        )
        right_ok = (
            not _is_ascii_word_char(alias[-1])
            or end == len(text)
            or not _is_ascii_word_char(text[end])
        )
        # Avoid matching the single-letter C against C++/C#/C-style names.
        if (
            len(alias) == 1
            and alias[0].isascii()
            and alias[0].isalpha()
            and end < len(text)
            and text[end] in "+#./"
        ):
            right_ok = False
        if left_ok and right_ok:
            return True
        start = index + 1


def _contains_skill_token(text: str, names: Iterable[str]) -> bool:
    normalized_text = _normalized(text)
    return any(
        _skill_token_occurs(normalized_text, name)
        for name in names
        if name
    )


def _skill_match_names(
    item: SkillItem,
    normalized_skill: Any,
    aliases_by_skill: Mapping[str, Iterable[str]] | None,
) -> list[str]:
    names: list[str] = [item.name]
    if normalized_skill is not None:
        names.append(normalized_skill.source_name)
        if normalized_skill.canonical_name:
            names.append(normalized_skill.canonical_name)
        if normalized_skill.skill_id and aliases_by_skill:
            names.extend(aliases_by_skill.get(normalized_skill.skill_id, ()))
    elif aliases_by_skill:
        names.extend(aliases_by_skill.get(item.item_id, ()))

    unique: dict[str, None] = {}
    for name in names:
        normalized = _normalized(name)
        if normalized:
            unique.setdefault(normalized, None)
    return list(unique)


def _explicit_skill_evidence_matches(
    item: SkillItem,
    evidence: Evidence,
    text: str,
) -> bool:
    item_quote = _normalized(item.evidence.quote)
    task_quote = _normalized(evidence.quote)
    if item_quote and (
        _skill_token_occurs(task_quote, item_quote)
        or _skill_token_occurs(_normalized(text), item_quote)
    ):
        return True
    return (
        item.evidence.source_id == evidence.source_id
        and item.evidence.start is not None
        and evidence.start is not None
        and item.evidence.end is not None
        and evidence.end is not None
        and item.evidence.start <= evidence.end
        and evidence.start <= item.evidence.end
    )


def _skill_relation_values(
    skill_task_relations: Mapping[str, Iterable[str | int]] | None,
    item: SkillItem,
    normalized_skill: Any,
) -> list[str | int]:
    if not skill_task_relations:
        return []
    keys = [item.item_id]
    if normalized_skill is not None and normalized_skill.skill_id:
        keys.append(normalized_skill.skill_id)
    values: list[str | int] = []
    for key in keys:
        entry = skill_task_relations.get(key)
        if entry is None:
            continue
        if isinstance(entry, (str, int)):
            values.append(entry)
        else:
            values.extend(entry)
    return values


def _structured_relation_matches(
    task_index: int,
    text: str,
    evidence: Evidence,
    relation_values: Iterable[str | int],
) -> bool:
    normalized_text = _normalized(text)
    normalized_evidence = _normalized(evidence.quote)
    for relation in relation_values:
        if isinstance(relation, int):
            if relation == task_index:
                return True
            continue
        relation_text = str(relation).strip()
        if relation_text.isdigit() and int(relation_text) == task_index:
            return True
        normalized_relation = _normalized(relation_text)
        if normalized_relation and (
            normalized_relation == normalized_text
            or normalized_relation == normalized_evidence
            or normalized_relation in normalized_text
        ):
            return True
    return False


def _task_texts_for_skill(
    task_texts: list[tuple[str, Evidence]],
    item: SkillItem,
    normalized_skill: Any,
    *,
    aliases_by_skill: Mapping[str, Iterable[str]] | None = None,
    skill_task_relations: Mapping[str, Iterable[str | int]] | None = None,
) -> list[tuple[str, Evidence]]:
    names = _skill_match_names(item, normalized_skill, aliases_by_skill)
    relation_values = _skill_relation_values(
        skill_task_relations,
        item,
        normalized_skill,
    )
    associated: list[tuple[str, Evidence]] = []
    for index, (text, evidence) in enumerate(task_texts):
        if _contains_skill_token(text, names):
            associated.append((text, evidence))
        elif _explicit_skill_evidence_matches(item, evidence, text):
            associated.append((text, evidence))
        elif _structured_relation_matches(
            index,
            text,
            evidence,
            relation_values,
        ):
            associated.append((text, evidence))
    return associated


def _context(entry: WorkEntry | ProjectEntry, texts: list[tuple[str, Evidence]]) -> list[str]:
    parts: list[str] = []
    if isinstance(entry, WorkEntry):
        parts.extend(
            value
            for value in (
                entry.company,
                entry.position,
                entry.department,
                entry.location,
            )
            if value
        )
    else:
        parts.extend(
            value
            for value in (entry.name, entry.role, entry.affiliation)
            if value
        )
    parts.extend(text for text, _ in texts)
    return parts


def _infer_ownership(texts: list[str]) -> str:
    if not texts:
        return "unknown"
    joined = " ".join(texts)
    if _LEAD_PATTERN.search(joined):
        return "led"
    if _DESIGN_PATTERN.search(joined):
        return "designed"
    if _OWNED_PATTERN.search(joined):
        return "owned"
    if _IMPLEMENT_PATTERN.search(joined):
        return "implemented"
    if _PARTICIPATE_PATTERN.search(joined):
        return "participated"
    return "participated"


def _infer_level(scope: str, item: SkillItem, texts: list[str]) -> str:
    if scope == "skills":
        joined = item.evidence.quote
        return "course_used" if _COURSE_PATTERN.search(joined) else "declared_only"
    joined = " ".join(texts)
    if texts:
        if _MEASURABLE_PATTERN.search(joined):
            return "measured_result"
        if _DESIGN_PATTERN.search(joined):
            return "designed_system"
        if _LEAD_PATTERN.search(joined) or _OWNED_PATTERN.search(joined):
            return "owned_component"
    return "work_used" if scope.startswith("work_experience:") else "project_used"


def _infer_depth(scope: str, texts: list[str], ownership: str) -> str:
    if ownership == "led":
        return "led"
    if ownership == "designed":
        return "designed"
    if ownership in {"owned", "implemented"}:
        return "implemented"
    if texts or scope.startswith(("work_experience:", "project_experience:")):
        return "used"
    return "declared"


def build_capability_evidence_profiles(
    extraction: CVExtractionResult,
    normalized: CVNormalizedResult,
    *,
    as_of_date: date,
    taxonomy_version: str = "2.0",
    created_from_snapshot: str = "cv-confirmed-snapshot.v1",
    aliases_by_skill: Mapping[str, Iterable[str]] | None = None,
    skill_task_relations: Mapping[str, Iterable[str | int]] | None = None,
) -> CVCapabilityEvidenceProfileResult:
    normalized_by_item = {item.source_item_id: item for item in normalized.normalized_skills}
    raw_items: list[tuple[str, SkillItem, Any, list[tuple[str, Evidence]]]] = []
    for item in extraction.skills:
        raw_items.append(("skills", item, None, []))
    for entry in sorted(extraction.work_experience, key=lambda item: item.entry_id):
        for item in sorted(entry.tech_stack, key=lambda value: value.item_id):
            raw_items.append(
                (
                    f"work_experience:{entry.entry_id}:tech_stack",
                    item,
                    entry,
                    _task_texts(entry),
                )
            )
    for entry in sorted(extraction.project_experience, key=lambda item: item.entry_id):
        for item in sorted(entry.tech_stack, key=lambda value: value.item_id):
            raw_items.append(
                (
                    f"project_experience:{entry.entry_id}:tech_stack",
                    item,
                    entry,
                    _task_texts(entry),
                )
            )

    evidence_items: list[CapabilityEvidenceItem] = []
    for index, (scope, item, entry, task_texts) in enumerate(raw_items):
        skill_id, skill_name = _skill_display(item, normalized_by_item)
        normalized_skill = normalized_by_item.get(item.item_id)
        associated_task_texts = _task_texts_for_skill(
            task_texts,
            item,
            normalized_skill,
            aliases_by_skill=aliases_by_skill,
            skill_task_relations=skill_task_relations,
        )
        texts = [text for text, _ in associated_task_texts]
        ownership = _infer_ownership(texts)
        evidence_item = CapabilityEvidenceItem(
            evidence_item_id=_stable_id("cap_ev", extraction.document_id, f"{index:04d}"),
            document_id=extraction.document_id,
            skill_id=skill_id,
            skill_name=skill_name,
            evidence_level=_infer_level(scope, item, texts),
            context=_context(entry, associated_task_texts) if entry is not None else ["技能栏声明"],
            ownership=ownership,
            depth=_infer_depth(scope, texts, ownership),
            recency=_recency(entry.date if entry is not None else None, as_of_date),
            source_scope=scope,
            source_experience_id=(
                str(entry.entry_id)
                if isinstance(entry, WorkEntry)
                else None
            ),
            source_project_id=(
                str(entry.entry_id)
                if isinstance(entry, ProjectEntry)
                else None
            ),
            source_evidence=item.evidence,
            evidence_lineage=_unique_evidence(
                [item.evidence, *(evidence for _, evidence in associated_task_texts)]
            ),
            source_text=(
                " | ".join(texts) if texts else item.evidence.quote
            ),
        )
        evidence_items.append(evidence_item)

    groups: dict[tuple[str, ...], list[CapabilityEvidenceItem]] = {}
    for item in evidence_items:
        key = (
            "canonical"
            if item.skill_id
            else "raw",
            item.skill_id or item.skill_name,
            item.skill_name,
        )
        groups.setdefault(key, []).append(item)

    profiles: list[CapabilityEvidenceProfile] = []
    for key in sorted(groups):
        items = groups[key]
        representative = items[0]
        sorted_items = sorted(
            items,
            key=lambda item: (
                EVIDENCE_LEVEL_ORDER[item.evidence_level],
                OWNERSHIP_ORDER[item.ownership],
                DEPTH_ORDER[item.depth],
                RECENCY_ORDER[item.recency],
                item.source_scope,
                item.evidence_item_id,
            ),
            reverse=True,
        )
        strongest = sorted_items[0]
        profiles.append(
            CapabilityEvidenceProfile(
                capability_id=_stable_id(
                    "cap_ev_profile", extraction.document_id, *key
                ),
                document_id=extraction.document_id,
                skill_id=representative.skill_id,
                skill_name=representative.skill_name,
                evidence_count=len(items),
                strongest_evidence=strongest,
                evidence_items=sorted_items,
            )
        )
    return CVCapabilityEvidenceProfileResult(
        document_id=extraction.document_id,
        taxonomy_version=taxonomy_version,
        derivation_version=CAPABILITY_EVIDENCE_DERIVATION_VERSION,
        created_from_snapshot=created_from_snapshot,
        as_of_date=as_of_date.isoformat(),
        profiles=profiles,
    )
