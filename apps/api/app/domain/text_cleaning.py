from __future__ import annotations

import re
import unicodedata


_CJK = "\u4e00-\u9fff"
# Mirrors Extraction validator: these platform tokens are artifacts everywhere.
_ARTIFACT_PATTERNS = tuple(
    re.compile(re.escape(artifact), re.IGNORECASE)
    for artifact in ("来自BOSS直聘", "kanzhun")
)
# Mirrors Extraction validator: these are legal BOSS terms and must survive.
_LEGAL_BOSS_PATTERN = re.compile(r"boss\s*直聘|boss\s*主页", re.IGNORECASE)
_INSERTED_BOSS_PATTERN = re.compile(
    rf"(?:(?<=[{_CJK}])boss|boss(?=[{_CJK}]))",
    re.IGNORECASE,
)
_INSERTED_ZHIPIN_PATTERN = re.compile(rf"(?<=[{_CJK}])直聘(?=[{_CJK}])")
_WHITESPACE_PATTERN = re.compile(r"[ \t]+")
_BOSS_LEGAL = "\ue000"
_BOSS_HOME_LEGAL = "\ue001"


def clean_jd_text(value: str) -> str:
    """Canonical pre-extraction cleaning: NFKC plus watermark removal."""
    if not value:
        return value
    text = unicodedata.normalize("NFKC", value)
    for pattern in _ARTIFACT_PATTERNS:
        text = pattern.sub("", text)

    def protect(match: re.Match[str]) -> str:
        return _BOSS_LEGAL if "直聘" in match.group(0) else _BOSS_HOME_LEGAL

    text = _LEGAL_BOSS_PATTERN.sub(protect, text)
    text = _INSERTED_BOSS_PATTERN.sub("", text)
    text = _INSERTED_ZHIPIN_PATTERN.sub("", text)
    text = text.replace(_BOSS_LEGAL, "BOSS直聘").replace(
        _BOSS_HOME_LEGAL, "BOSS主页"
    )
    return text


def clean_jd_text_for_display(value: str) -> str:
    """Display cleaning: canonical cleaning plus whitespace collapse."""
    if not value:
        return value
    text = clean_jd_text(value)
    text = _WHITESPACE_PATTERN.sub(" ", text)
    return text.strip()
