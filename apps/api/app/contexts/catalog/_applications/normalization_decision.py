"""Gold-free Normalization decision policy.

The decision only reads ranked suggestions and never sees evaluation gold.
Thresholds are chosen on a dev split and frozen before the holdout test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.contexts.catalog._ports.normalization_suggestions import (
    NormalizationSuggestion,
)


@dataclass(frozen=True)
class NormalizationDecisionPolicy:
    exact_lexical_accept: float = 0.98
    combined_threshold: float = 0.85
    semantic_threshold: float = 0.75
    margin_threshold: float = 0.10
    semantic_rescue_threshold: float = 0.85
    semantic_rescue_margin: float = 0.15


def decide_normalization(
    suggestions: Sequence[NormalizationSuggestion],
    lexical_suggestions: Sequence[NormalizationSuggestion],
    *,
    policy: NormalizationDecisionPolicy,
) -> str:
    """Return ``auto_accept`` or ``review`` without reading any gold label."""

    if not suggestions:
        return "review"
    top = suggestions[0]
    if (top.lexical_score or 0.0) >= policy.exact_lexical_accept:
        return "auto_accept"
    if not top.semantic_available or (top.semantic_score or 0.0) <= 0.0:
        return "review"
    second = suggestions[1] if len(suggestions) > 1 else None
    margin = abs(top.combined_score - (second.combined_score if second else 0.0))
    lexical_top_id = (
        lexical_suggestions[0].skill_id if lexical_suggestions else None
    )
    lexical_top_ids = {item.skill_id for item in lexical_suggestions}
    if (
        top.skill_id == lexical_top_id
        and (top.combined_score or 0.0) >= policy.combined_threshold
        and (top.semantic_score or 0.0) >= policy.semantic_threshold
        and margin >= policy.margin_threshold
    ):
        return "auto_accept"
    if (
        top.skill_id not in lexical_top_ids
        and (top.semantic_score or 0.0) >= policy.semantic_rescue_threshold
        and margin >= policy.semantic_rescue_margin
    ):
        return "auto_accept"
    return "review"


__all__ = ["NormalizationDecisionPolicy", "decide_normalization"]
