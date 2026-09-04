"""Shared degree normalization and rank definitions.

Single source of truth for education levels used by the production matcher
and the hard-condition evaluation. Do not maintain a second enum in Gold
tooling or evaluation adapters.
"""

from __future__ import annotations

import unicodedata

DEGREE_LEVELS: tuple[str, ...] = (
    "high_school",
    "associate",
    "bachelor",
    "master",
    "doctor",
    "postdoc",
)

# Chinese and English aliases accepted by normalize_degree(). Order matters
# only for clarity; containment matching is used for Chinese text and exact
# token matching for short English aliases.
_DEGREE_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("high_school", ("high school", "高中", "高中学历")),
    ("associate", ("associate", "大专", "专科", "college", "diploma")),
    ("bachelor", ("bachelor", "本科", "学士", "undergraduate")),
    ("master", ("master", "硕士", "研究生")),
    ("postdoc", ("postdoc", "postdoctoral", "博士后")),
    ("doctor", ("doctor", "phd", "ph.d.", "博士")),
)

_ENGLISH_TOKEN_ALIASES: dict[str, str] = {
    "associate": "associate",
    "bachelor": "bachelor",
    "master": "master",
    "doctor": "doctor",
    "phd": "doctor",
    "ph.d.": "doctor",
    "postdoc": "postdoc",
    "postdoctoral": "postdoc",
    "undergraduate": "bachelor",
    "college": "associate",
    "diploma": "associate",
    "high school": "high_school",
}


def normalize_degree(value: str | None) -> str | None:
    """Return the canonical degree level for a raw value, or None."""
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value)).strip().casefold()
    if not text:
        return None
    if text in DEGREE_LEVELS:
        return text
    if text in _ENGLISH_TOKEN_ALIASES:
        return _ENGLISH_TOKEN_ALIASES[text]
    for level, aliases in _DEGREE_ALIASES:
        for alias in aliases:
            if alias in text:
                return level
    return None


def degree_rank(value: str | None) -> int | None:
    """Rank of a degree value using the shared level order."""
    level = normalize_degree(value)
    if level is None:
        return None
    return DEGREE_LEVELS.index(level)


def parse_degree_from_text(text: str | None) -> str | None:
    """Extract the highest degree mentioned in free text."""
    if not text:
        return None
    found: list[int] = []
    for token in (
        "博士后",
        "postdoc",
        "博士",
        "phd",
        "ph.d.",
        "doctor",
        "硕士",
        "研究生",
        "master",
        "本科",
        "学士",
        "bachelor",
        "undergraduate",
        "大专",
        "专科",
        "associate",
        "college",
        "diploma",
        "高中",
        "high school",
    ):
        if token.casefold() in str(text).casefold():
            rank = degree_rank(token)
            if rank is not None:
                found.append(rank)
    if not found:
        return None
    return DEGREE_LEVELS[max(found)]
