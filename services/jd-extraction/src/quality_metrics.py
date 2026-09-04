"""Deterministic field-level metrics for the fixed JD quality evaluation."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

_TOKEN_PATTERN = re.compile(r"[a-z0-9\u4e00-\u9fff]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_PATTERN.findall(text.lower()))


def token_f1(predicted: str, expected: str) -> float:
    predicted_tokens = _tokens(predicted)
    expected_tokens = _tokens(expected)
    if not expected_tokens:
        return 1.0 if not predicted_tokens else 0.0
    if not predicted_tokens:
        return 0.0
    overlap = len(predicted_tokens & expected_tokens)
    precision = overlap / len(predicted_tokens)
    recall = overlap / len(expected_tokens)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def best_span_f1(predicted_spans: Iterable[str], expected_span: str) -> float:
    return max(
        (token_f1(predicted, expected_span) for predicted in predicted_spans),
        default=0.0,
    )


def _normalize_skill(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.lower())


def _set_f1(
    predicted: Iterable[str], expected: Iterable[str]
) -> tuple[float, float, float]:
    predicted_set = {_normalize_skill(item) for item in predicted}
    expected_set = {_normalize_skill(item) for item in expected}
    overlap = len(predicted_set & expected_set)
    precision = (
        overlap / len(predicted_set)
        if predicted_set
        else (1.0 if not expected_set else 0.0)
    )
    recall = (
        overlap / len(expected_set)
        if expected_set
        else (1.0 if not predicted_set else 0.0)
    )
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return round(precision, 4), round(recall, 4), round(f1, 4)


def _average(values: Iterable[float]) -> float:
    known = list(values)
    return round(sum(known) / len(known), 4) if known else 1.0


def compute_quality_metrics(
    predicted: Mapping[str, Any],
    expected: Mapping[str, Any],
    raw_text: str,
) -> dict[str, Any]:
    precision, recall, skill_f1 = _set_f1(
        [str(item) for item in predicted.get("skills", [])],
        [str(item) for item in expected.get("skills", [])],
    )
    responsibility_f1 = _average(
        best_span_f1(predicted.get("responsibility_spans", []), span)
        for span in expected.get("responsibilities", [])
    )
    requirement_f1 = _average(
        best_span_f1(predicted.get("requirement_spans", []), span)
        for span in expected.get("requirements", [])
    )
    if expected.get("responsibilities") or expected.get("requirements"):
        span_f1 = round((responsibility_f1 + requirement_f1) / 2, 4)
    else:
        span_f1 = 1.0
    evidence_quotes = [str(item) for item in predicted.get("evidence_quotes", [])]
    exact_quotes = [quote for quote in evidence_quotes if quote and quote in raw_text]
    hallucination_cases = [
        quote for quote in evidence_quotes if quote and quote not in raw_text
    ]
    evidence_exact_rate = (
        round(len(exact_quotes) / len(evidence_quotes), 4)
        if evidence_quotes
        else 1.0
    )
    return {
        "skill_precision": precision,
        "skill_recall": recall,
        "skill_f1": skill_f1,
        "responsibility_span_f1": responsibility_f1,
        "requirement_span_f1": requirement_f1,
        "span_f1": span_f1,
        "evidence_exact_rate": evidence_exact_rate,
        "schema_failed": bool(predicted.get("schema_failed", False)),
        "hallucination_cases": hallucination_cases,
    }


__all__ = [
    "best_span_f1",
    "compute_quality_metrics",
    "token_f1",
]
