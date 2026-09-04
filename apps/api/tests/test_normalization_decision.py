"""Gold-free Normalization decision policy regression tests."""

from __future__ import annotations

import inspect

from app.contexts.catalog._applications.normalization_decision import (
    NormalizationDecisionPolicy,
    decide_normalization,
)
from app.contexts.catalog._ports.normalization_suggestions import (
    NormalizationSuggestion,
)


BANNED_PARAMS = {"gold_id", "expected_skill_id", "gold_available"}


def _suggestion(
    skill_id: str,
    lexical_score: float = 0.0,
    semantic_score: float | None = None,
    combined_score: float = 0.0,
    semantic_available: bool = True,
) -> NormalizationSuggestion:
    return NormalizationSuggestion(
        skill_id=skill_id,
        skill_name=skill_id,
        category=None,
        rank=1,
        lexical_score=lexical_score,
        semantic_score=semantic_score,
        combined_score=combined_score,
        matched_alias=None,
        reasons=(),
        semantic_available=semantic_available,
    )


def test_decision_signature_has_no_gold_parameters() -> None:
    parameters = inspect.signature(decide_normalization).parameters
    assert not (BANNED_PARAMS & set(parameters))


def test_exact_lexical_alias_accepts() -> None:
    suggestions = [
        _suggestion("s1", lexical_score=0.98, semantic_score=0.30, combined_score=0.65)
    ]
    assert (
        decide_normalization(
            suggestions,
            suggestions,
            policy=NormalizationDecisionPolicy(),
        )
        == "auto_accept"
    )


def test_combined_semantic_margin_accepts() -> None:
    suggestions = [
        _suggestion("s1", lexical_score=0.6, semantic_score=0.8, combined_score=0.9),
        _suggestion("s2", lexical_score=0.5, semantic_score=0.4, combined_score=0.6),
    ]
    assert (
        decide_normalization(
            suggestions,
            suggestions,
            policy=NormalizationDecisionPolicy(),
        )
        == "auto_accept"
    )


def test_low_margin_reviews() -> None:
    suggestions = [
        _suggestion("s1", lexical_score=0.6, semantic_score=0.8, combined_score=0.85),
        _suggestion("s2", lexical_score=0.6, semantic_score=0.78, combined_score=0.84),
    ]
    assert (
        decide_normalization(
            suggestions,
            suggestions,
            policy=NormalizationDecisionPolicy(),
        )
        == "review"
    )


def test_semantic_rescue_accepts_without_lexical_agreement() -> None:
    lexical = [_suggestion("s2", lexical_score=0.9, combined_score=0.9)]
    suggestions = [
        _suggestion("s1", lexical_score=0.1, semantic_score=0.9, combined_score=0.85),
        _suggestion("s2", lexical_score=0.9, semantic_score=0.3, combined_score=0.7),
    ]
    assert (
        decide_normalization(
            suggestions,
            lexical,
            policy=NormalizationDecisionPolicy(),
        )
        == "auto_accept"
    )


def test_empty_suggestions_review() -> None:
    assert (
        decide_normalization(
            (),
            (),
            policy=NormalizationDecisionPolicy(),
        )
        == "review"
    )
