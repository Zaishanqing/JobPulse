from __future__ import annotations

import json
import unicodedata
from copy import deepcopy
from typing import Any

from app.contexts.cv_ingestion.domain import CVFieldDecision
from app.domain.json_types import JsonObject


class CVReviewRuleViolation(ValueError):
    pass


def _iter_evidence(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "evidence" and isinstance(child, dict):
                yield child
            else:
                yield from _iter_evidence(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_evidence(child)


def validate_confirmed_evidence(payload: JsonObject, raw_text: str) -> None:
    normalized_text = unicodedata.normalize("NFKC", raw_text)
    for evidence in _iter_evidence(payload):
        quote = evidence.get("quote")
        alignment = evidence.get("alignment", "exact")
        if not isinstance(quote, str) or not quote:
            raise CVReviewRuleViolation("Confirmed evidence must contain a quote")
        if alignment in {"unresolved", "review_required"}:
            continue
        start = evidence.get("start")
        end = evidence.get("end")
        if not isinstance(start, int) or not isinstance(end, int):
            raise CVReviewRuleViolation("Confirmed evidence must contain start/end offsets")
        if start < 0 or end < start or end > len(normalized_text):
            raise CVReviewRuleViolation("Confirmed evidence offsets are invalid")
        if alignment == "exact" and normalized_text[start:end] != unicodedata.normalize(
            "NFKC", quote
        ):
            raise CVReviewRuleViolation("Confirmed evidence quote does not match raw text")


def apply_field_decisions(
    extraction: JsonObject,
    normalized: JsonObject,
    decisions: tuple[CVFieldDecision, ...],
) -> tuple[JsonObject, JsonObject]:
    confirmed_extraction = deepcopy(extraction)
    confirmed_normalized = deepcopy(normalized)
    normalized_skills = confirmed_normalized.get("normalized_skills", [])
    normalized_by_source = {
        item.get("source_item_id"): item
        for item in normalized_skills
        if isinstance(item, dict)
    }
    extraction_items = _extraction_items(confirmed_extraction)

    for decision in decisions:
        item_id = decision.item_id or decision.field_id
        if decision.decision == "accept":
            if item_id.startswith(("new_patent_", "new_education_")):
                raise CVReviewRuleViolation(
                    "missing item placeholder must be corrected, marked unknown, or removed"
                )
            continue
        item = extraction_items.get(item_id)
        if decision.decision == "remove":
            if decision.field_path:
                if item is not None:
                    _remove_field(item, decision.section, decision.field_path)
            else:
                _remove_item(confirmed_extraction, item_id)
            if decision.section == "skills" and decision.field_path in {None, "name"}:
                normalized_by_source.pop(item_id, None)
            if decision.section == "work_experience" and decision.field_path == "position":
                _invalidate_position_classification(item, source_title=None)
            continue
        if decision.decision == "unknown":
            if decision.field_path and item is not None:
                _mark_field_unknown(item, decision.field_path)
            if decision.section == "skills":
                skill = normalized_by_source.get(item_id)
                if skill is not None:
                    skill["resolution_status"] = "unresolved"
                    skill["skill_id"] = None
                    skill["canonical_name"] = None
                    skill["normalization_confidence"] = None
                    skill["resolution_source"] = "unresolved"
            if decision.section == "work_experience" and decision.field_path == "position":
                _invalidate_position_classification(item, source_title=None)
            continue
        if decision.decision == "correct":
            if not decision.corrected_value:
                raise CVReviewRuleViolation("correct decision requires corrected_value")
            if not decision.evidence_quote:
                raise CVReviewRuleViolation("correct decision requires evidence")
            if not decision.correction_reason:
                raise CVReviewRuleViolation("correct decision requires correction_reason")
            if item is None:
                item = _create_missing_review_item(
                    confirmed_extraction, decision, item_id
                )
                extraction_items[item_id] = item
            _apply_correction(confirmed_extraction, item, decision)
            if decision.section == "work_experience" and decision.field_path == "position":
                _invalidate_position_classification(
                    confirmed_normalized,
                    item_id,
                    decision.corrected_value,
                )
            if decision.section == "skills" and decision.field_path == "name":
                skill = normalized_by_source.get(item_id)
                if skill is not None:
                    skill["source_name"] = decision.corrected_value
                    skill["resolution_status"] = "unresolved"
                    skill["skill_id"] = None
                    skill["canonical_name"] = None
                    skill["normalization_confidence"] = None
                    skill["resolution_source"] = "unresolved"
            _apply_corrected_evidence(item, decision)
            continue
        raise CVReviewRuleViolation(f"unsupported review decision: {decision.decision}")

    confirmed_normalized["normalized_skills"] = [
        item
        for item in normalized_skills
        if item.get("source_item_id") in normalized_by_source
    ]
    return confirmed_extraction, confirmed_normalized


_NEW_EDUCATION_FIELD_PATHS = frozenset(
    {
        "school",
        "college",
        "major",
        "degree",
        "date.start",
        "date.end",
        "gpa",
        "gpa_scale",
        "location",
        "school_tag",
    }
)


def _create_missing_review_item(
    extraction: dict,
    decision: CVFieldDecision,
    item_id: str,
) -> dict:
    """Create only the explicit placeholders exposed by the review API."""
    if (
        decision.section == "patents"
        and decision.field_path == "title"
        and item_id.startswith("new_patent_")
    ):
        item = {
            "entry_id": item_id,
            "title": decision.corrected_value,
            "status": "unknown",
        }
        patents = extraction.setdefault("patents", [])
        if not isinstance(patents, list):
            raise CVReviewRuleViolation("patents collection is invalid")
        patents.append(item)
        return item
    if decision.section == "education" and item_id.startswith("new_education_"):
        if decision.field_path not in _NEW_EDUCATION_FIELD_PATHS:
            raise CVReviewRuleViolation("review decision references a missing item")
        # 逐字段补录：先建空条目，再由 _apply_correction 按 field_path 写入结构化字段。
        item = {"entry_id": item_id}
        education = extraction.setdefault("education", [])
        if not isinstance(education, list):
            raise CVReviewRuleViolation("education collection is invalid")
        education.append(item)
        return item
    raise CVReviewRuleViolation("review decision references a missing item")


def _extraction_items(extraction: dict) -> dict[str, dict]:
    items: dict[str, dict] = {}
    personal_info = extraction.get("personal_info")
    if isinstance(personal_info, dict):
        items["personal_info"] = personal_info
    for section in (
        "education",
        "work_experience",
        "project_experience",
        "skills",
        "languages",
        "certificates",
        "awards",
        "publications",
        "patents",
        "research_outputs",
        "self_evaluation",
    ):
        for value in extraction.get(section, []):
            if not isinstance(value, dict):
                continue
            field_id = value.get("entry_id") or value.get("item_id")
            if isinstance(field_id, str) and field_id:
                items[field_id] = value
    return items


def _remove_item(extraction: dict, field_id: str) -> None:
    for section in (
        "education",
        "work_experience",
        "project_experience",
        "skills",
        "languages",
        "certificates",
        "awards",
        "publications",
        "patents",
        "research_outputs",
        "self_evaluation",
    ):
        values = extraction.get(section, [])
        extraction[section] = [
            value
            for value in values
            if not (
                isinstance(value, dict)
                and (
                    value.get("entry_id") == field_id
                    or value.get("item_id") == field_id
                )
            )
        ]


def _apply_correction(extraction: dict, item: dict, decision: CVFieldDecision) -> None:
    if decision.field_path:
        _set_field(item, decision.field_path, decision.corrected_value)
        return
    field_name = decision.field_type
    if field_name in {"skill", "project"}:
        item["name"] = decision.corrected_value
        return
    if field_name in item:
        item[field_name] = decision.corrected_value
        return
    item["name"] = decision.corrected_value


def _set_field(item: dict, field_path: str, value: str | None) -> None:
    target = item
    segments = field_path.split(".")
    for segment in segments[:-1]:
        child = target.get(segment)
        if not isinstance(child, dict):
            child = {}
            target[segment] = child
        target = child
    target[segments[-1]] = value


def _remove_field(item: dict, section: str, field_path: str) -> None:
    required = {
        "personal_info": set(),
        "education": {"school", "major", "degree"},
        "work_experience": {"company"},
        "project_experience": {"name"},
        "skills": {"name"},
        "languages": {"language", "proficiency"},
        "certificates": {"name", "kind"},
        "awards": {"name"},
        "publications": {"title", "status"},
        "patents": {"title", "status"},
        "research_outputs": {"name", "output_type"},
        "self_evaluation": {"content"},
    }
    # Required fields cannot be silently deleted because that would make the
    # confirmed snapshot violate the extraction contract.
    if field_path in required.get(section, set()):
        raise CVReviewRuleViolation(
            f"required field cannot be removed: {section}.{field_path}"
        )
    _set_field(item, field_path, None)


def _mark_field_unknown(item: dict, field_path: str) -> None:
    unknown_capable = {"degree", "work_type", "proficiency", "level", "status"}
    if field_path.split(".")[-1] in unknown_capable:
        _set_field(item, field_path, "unknown")


def _apply_corrected_evidence(item: dict | None, decision: CVFieldDecision) -> None:
    if item is None or not decision.evidence_quote:
        return
    evidence = {
        "source_id": "user_correction",
        "quote": decision.evidence_quote,
        "start": decision.evidence_start,
        "end": decision.evidence_end,
        "alignment": "exact",
        "occurrence_index": 0,
    }
    root_field = decision.field_path.split(".", 1)[0] if decision.field_path else None
    bindings = item.get("field_evidence")
    if root_field and isinstance(bindings, list):
        for binding in bindings:
            if isinstance(binding, dict) and binding.get("field_name") == root_field:
                binding["evidence"] = evidence
                return
    item["evidence"] = evidence


def _invalidate_position_classification(
    normalized: dict,
    item_id: str,
    corrected_title: str | None,
) -> None:
    for item in normalized.get("position_classifications", []):
        if not isinstance(item, dict) or item.get("source_object_id") != item_id:
            continue
        classification = item.get("job_classification")
        if not isinstance(classification, dict):
            continue
        classification.update(
            {
                "source_title": corrected_title,
                "position_id": None,
                "position_code": None,
                "position_name": None,
                "family_code": None,
                "family_name": None,
                "candidate_positions": [],
                "confidence": None,
                "classification_status": "catalog_gap",
                "review_reason_codes": ["user_corrected_source_title"],
                "evidence_refs": [],
            }
        )
