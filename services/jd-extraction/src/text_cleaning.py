"""Formal pre-extraction text cleaning stage.

The cleaned text becomes the canonical source for extraction and Evidence:
platform watermarks are removed before the model sees the JD, while legal
terms such as ``BOSS直聘`` or ``BOSS主页`` are preserved.
"""

from __future__ import annotations

import re
import unicodedata


_CJK = "\u4e00-\u9fff"
_ARTIFACT_PATTERNS = tuple(
    re.compile(re.escape(artifact), re.IGNORECASE)
    for artifact in ("来自BOSS直聘", "kanzhun")
)
_LEGAL_BOSS_PATTERN = re.compile(r"boss\s*直聘|boss\s*主页", re.IGNORECASE)
_INSERTED_BOSS_FULL_PATTERN = re.compile(
    rf"(?:(?<=[{_CJK}])boss\s*直聘(?=[{_CJK}])"
    rf"|(?<![{_CJK}])boss\s*直聘(?=[{_CJK}])"
    rf"|(?<=[{_CJK}])boss\s*直聘(?=[A-Za-z0-9]))",
    re.IGNORECASE,
)
_INSERTED_HOME_FULL_PATTERN = re.compile(
    rf"(?:(?<=[{_CJK}])boss\s*主页(?=[{_CJK}])"
    rf"|(?<![{_CJK}])boss\s*主页(?=[{_CJK}])"
    rf"|(?<=[{_CJK}])boss\s*主页(?=[A-Za-z0-9]))",
    re.IGNORECASE,
)
_INSERTED_BOSS_PATTERN = re.compile(
    rf"(?:(?<=[{_CJK}])boss|boss(?=[{_CJK}]))",
    re.IGNORECASE,
)
_INSERTED_ZHIPIN_PATTERN = re.compile(rf"(?<=[{_CJK}])直聘(?=[{_CJK}])")
_BOSS_LEGAL = "\ue000"
_BOSS_HOME_LEGAL = "\ue001"


def clean_jd_text(value: str) -> str:
    """Deterministic pre-extraction cleaning: NFKC plus watermark removal.

    Mirrors the Extraction validator rules: ``来自BOSS直聘`` and ``kanzhun``
    are removed everywhere; a full ``BOSS直聘``/``BOSS主页`` token glued into a
    Chinese word or prefixed to one is removed before legal terms are
    protected, so standalone recruiter/platform mentions survive.
    """
    if not value:
        return value
    text = unicodedata.normalize("NFKC", value)
    for pattern in _ARTIFACT_PATTERNS:
        text = pattern.sub("", text)
    text = _INSERTED_BOSS_FULL_PATTERN.sub("", text)
    text = _INSERTED_HOME_FULL_PATTERN.sub("", text)

    def protect(match: re.Match[str]) -> str:
        return _BOSS_LEGAL if "直聘" in match.group(0) else _BOSS_HOME_LEGAL

    text = _LEGAL_BOSS_PATTERN.sub(protect, text)
    text = _INSERTED_BOSS_PATTERN.sub("", text)
    text = _INSERTED_ZHIPIN_PATTERN.sub("", text)
    text = text.replace(_BOSS_LEGAL, "BOSS直聘").replace(
        _BOSS_HOME_LEGAL, "BOSS主页"
    )
    return text
