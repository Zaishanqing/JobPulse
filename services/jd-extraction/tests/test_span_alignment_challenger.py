"""Boundary regression tests for the span-first aligner challenger."""

from __future__ import annotations

import importlib.util
from pathlib import Path


_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "run_span_alignment_challenger.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "run_span_alignment_challenger",
    _MODULE_PATH,
)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_MODULE)


def test_fuzzy_window_at_text_end_does_not_index_past_last_token() -> None:
    aligned = _MODULE._align_span(
        "Go Python",
        "Java Python Go",
        preferred_offset=0,
    )
    assert aligned["alignment"] == "fuzzy"
    assert aligned["start"] >= 0
    assert aligned["end"] > aligned["start"]
    assert aligned["quote"] == "Python Go"


def test_exact_branch_prefers_occurrence_closest_to_original_offset() -> None:
    aligned = _MODULE._align_span(
        "Python",
        "Python Go Python",
        preferred_offset=8,
    )
    assert aligned["alignment"] == "exact"
    assert aligned["quote"] == "Python"
    assert aligned["start"] == 10
    assert aligned["end"] == 16


def test_normalized_branch_handles_fullwidth_punctuation_and_spaces() -> None:
    aligned = _MODULE._align_span(
        "Python、开发",
        "python, 开发",
        preferred_offset=0,
    )
    assert aligned["alignment"] == "normalized_exact"
    assert aligned["start"] == 0
    assert aligned["end"] == len("python, 开发")


def test_nfkc_ligature_maps_original_index_with_multichar_expansion() -> None:
    text = "efﬁcient Python"
    aligned = _MODULE._align_span(
        "efficient",
        text,
        preferred_offset=0,
    )
    assert aligned["alignment"] == "normalized_exact"
    assert aligned["quote"] == "efﬁcient"
    assert aligned["start"] == 0
    assert aligned["end"] == 8


def test_fuzzy_branch_aligns_reordered_chinese_tokens() -> None:
    aligned = _MODULE._align_span(
        "Python 精通 开发",
        "高级 精通 Python 开发 岗位",
        preferred_offset=0,
    )
    assert aligned["alignment"] == "fuzzy"
    assert "精通" in aligned["quote"]
    assert "Python" in aligned["quote"]
    assert "开发" in aligned["quote"]


def test_fuzzy_window_allows_length_variation() -> None:
    aligned = _MODULE._align_span(
        "开发工程师 Java",
        "高级 Java 开发工程师 岗位",
        preferred_offset=0,
    )
    assert aligned["alignment"] == "fuzzy"
    assert "Java" in aligned["quote"]
    assert "开发工程师" in aligned["quote"]


def test_fuzzy_chinese_bigram_tolerates_single_char_typo() -> None:
    aligned = _MODULE._align_span(
        "容器化部署",
        "熟悉容器化部暑与运维",
        preferred_offset=0,
    )
    assert aligned["alignment"] == "fuzzy"
    assert aligned["start"] >= 0
    assert aligned["end"] > aligned["start"]


def test_exact_branch_aligns_chinese_text_without_spaces() -> None:
    aligned = _MODULE._align_span(
        "精通Python",
        "熟悉Python语言并精通Python开发",
        preferred_offset=0,
    )
    assert aligned["alignment"] == "exact"
    assert aligned["quote"] == "精通Python"


def test_punctuation_perturbation_uses_normalized_branch() -> None:
    aligned = _MODULE._align_span(
        "Java/Spring Boot",
        "Java Spring Boot",
        preferred_offset=0,
    )
    assert aligned["alignment"] == "normalized_exact"
    assert aligned["quote"] == "Java Spring Boot"


def test_unresolved_quote_does_not_fabricate_span() -> None:
    aligned = _MODULE._align_span(
        "完全不存在的内容",
        "Java Python Go",
        preferred_offset=0,
    )
    assert aligned["alignment"] == "unresolved"
    assert aligned["start"] is None
    assert aligned["end"] is None
    assert aligned["quote"] is None
