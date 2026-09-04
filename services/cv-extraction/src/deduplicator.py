from __future__ import annotations

from .models import AwardEntry, CVExtractionResult, SkillItem
from .validator import _award_semantic_key


def _deduplicate_skills(items: list[SkillItem]) -> list[SkillItem]:
    """Keep one occurrence of the same skill identity within one semantic scope."""
    seen: set[tuple[str, str]] = set()
    kept: list[SkillItem] = []
    for item in items:
        key = (item.name.casefold(), item.item_type)
        if key not in seen:
            seen.add(key)
            kept.append(item)
    return kept


def _deduplicate_awards(items: list[AwardEntry]) -> list[AwardEntry]:
    """Keep the first exact semantic award occurrence."""
    seen: set[tuple[str, str | None]] = set()
    kept: list[AwardEntry] = []
    for item in items:
        key = _award_semantic_key(item.name)
        if key not in seen:
            seen.add(key)
            kept.append(item)
    return kept


def deduplicate_extraction(result: CVExtractionResult) -> CVExtractionResult:
    result.skills = _deduplicate_skills(result.skills)
    for work in result.work_experience:
        work.tech_stack = _deduplicate_skills(work.tech_stack)
    for project in result.project_experience:
        project.tech_stack = _deduplicate_skills(project.tech_stack)
    result.awards = _deduplicate_awards(result.awards)
    return result
