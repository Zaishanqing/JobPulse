from __future__ import annotations

import re
from collections.abc import Callable

from .semantic_rules import deterministic_validation_rules


_COMPOSITE_RULES = deterministic_validation_rules()["composite_skill"]
COMPOSITE_SKILL_ALLOWLIST = frozenset(_COMPOSITE_RULES["fixed_terms"])
SHARED_SEMANTIC_SEPARATORS = tuple(
    _COMPOSITE_RULES["taxonomy_confirmed_shared_separators"]
)
if not SHARED_SEMANTIC_SEPARATORS or any(
    not isinstance(separator, str) or not separator
    for separator in SHARED_SEMANTIC_SEPARATORS
):
    raise ValueError(
        "composite_skill.taxonomy_confirmed_shared_separators must contain non-empty strings"
    )

VERSION_SHORTHAND_PATTERN = re.compile(
    r"^(?P<prefix>.*?\D)(?P<first>\d{1,2}(?:\.\d+)?)\s*/\s*(?P<second>\d{1,2}(?:\.\d+)?)$"
)
ASCII_SKILL_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .+#_-]*$")
SHARED_SEMANTIC_SEPARATOR_PATTERN = re.compile(
    "|".join(re.escape(separator) for separator in SHARED_SEMANTIC_SEPARATORS)
)


def split_composite_skill_name(name: str) -> list[str] | None:
    """Return syntactically certain atomic parts for a slash-separated skill name."""
    if "/" not in name or any(term in name for term in COMPOSITE_SKILL_ALLOWLIST):
        return None
    version_match = VERSION_SHORTHAND_PATTERN.fullmatch(name.strip())
    if version_match is not None:
        prefix = version_match.group("prefix")
        return [
            f"{prefix}{version_match.group('first')}".strip(),
            f"{prefix}{version_match.group('second')}".strip(),
        ]
    parts = [part.strip() for part in name.split("/") if part.strip()]
    if len(parts) < 2:
        return None
    if all(
        ASCII_SKILL_TOKEN_PATTERN.fullmatch(part) is not None
        and any(character.isalpha() for character in part)
        for part in parts
    ):
        return parts
    return None


def split_taxonomy_confirmed_shared_skill_name(
    name: str,
    is_known_skill: Callable[[str], bool],
) -> list[str] | None:
    """Split a Chinese shared-affix expression only when taxonomy confirms every part."""
    raw_parts = [part.strip() for part in SHARED_SEMANTIC_SEPARATOR_PATTERN.split(name)]
    if len(raw_parts) < 2 or any(not part for part in raw_parts):
        return None
    if all(is_known_skill(part) for part in raw_parts):
        return raw_parts

    last = raw_parts[-1]
    for suffix_start in range(1, len(last)):
        suffix = last[suffix_start:]
        candidates = [f"{part}{suffix}" for part in raw_parts[:-1]] + [last]
        if len(set(candidates)) == len(candidates) and all(
            is_known_skill(candidate) for candidate in candidates
        ):
            return candidates

    first = raw_parts[0]
    for prefix_end in range(1, len(first)):
        prefix = first[:prefix_end]
        candidates = [first] + [f"{prefix}{part}" for part in raw_parts[1:]]
        if len(set(candidates)) == len(candidates) and all(
            is_known_skill(candidate) for candidate in candidates
        ):
            return candidates
    return None
