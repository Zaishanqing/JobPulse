from __future__ import annotations

from .models import JDExtractionResult, SkillRequirement


def deduplicate_extraction(result: JDExtractionResult) -> JDExtractionResult:
    for requirement in result.requirements:
        if not isinstance(requirement, SkillRequirement):
            continue
        seen: set[tuple[str, str]] = set()
        kept = []
        for item in requirement.items:
            key = (item.name.casefold(), item.item_type)
            if key not in seen:
                seen.add(key)
                kept.append(item)
        requirement.items = kept
    return result
