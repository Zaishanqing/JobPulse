from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from difflib import SequenceMatcher

from app.contexts.catalog._ports.normalization_suggestions import (
    CatalogEmbeddingError,
    CatalogEmbeddingPort,
    NormalizationSuggestion,
)
from app.contexts.catalog._ports.skills import SkillAliasRecord, SkillRecord
from app.domain.skills import normalize_skill_expression


LEXICAL_WEIGHT = 0.55
SEMANTIC_WEIGHT = 0.45
SEMANTIC_POOL_SIZE = 63
SEMANTIC_CHUNK_SIZE = 512
_WORD_RE = re.compile(r"[a-z0-9+#.]+|[\u3400-\u9fff]", re.IGNORECASE)
_COMPACT_RE = re.compile(r"[^a-z0-9+#\u3400-\u9fff]+", re.IGNORECASE)


@dataclass(frozen=True)
class _ScoredSkill:
    skill: SkillRecord
    lexical_score: float
    matched_alias: str | None
    reasons: tuple[str, ...]
    priority: int
    semantic_score: float | None = None
    combined_score: float = 0.0


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(_WORD_RE.findall(normalize_skill_expression(value)))


def _token_score(left: str, right: str) -> float:
    left_tokens = set(_tokens(left))
    right_tokens = set(_tokens(right))
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = len(left_tokens & right_tokens)
    if not intersection:
        return 0.0
    return 2 * intersection / (len(left_tokens) + len(right_tokens))


def _contains_on_boundary(shorter: str, longer: str) -> bool:
    short_tokens = _tokens(shorter)
    long_tokens = _tokens(longer)
    if not short_tokens or not long_tokens:
        return False
    compact = "".join(char for char in shorter if not char.isspace())
    if compact and all("\u3400" <= char <= "\u9fff" for char in compact):
        return normalize_skill_expression(shorter) in normalize_skill_expression(longer)
    if len(short_tokens) >= len(long_tokens):
        return False
    return any(
        long_tokens[index : index + len(short_tokens)] == short_tokens
        for index in range(len(long_tokens) - len(short_tokens) + 1)
    )


def _expression_score(raw: str, candidate: str) -> tuple[float, tuple[str, ...]]:
    normalized_raw = normalize_skill_expression(raw)
    normalized_candidate = normalize_skill_expression(candidate)
    if normalized_raw == normalized_candidate:
        return 1.0, ("标准名称精确命中",)
    if _COMPACT_RE.sub("", normalized_raw) == _COMPACT_RE.sub("", normalized_candidate):
        return 0.965, ("空格或标点变体",)
    token_score = _token_score(raw, candidate)
    sequence_score = SequenceMatcher(None, normalized_raw, normalized_candidate).ratio()
    score = 0.62 * sequence_score + 0.38 * token_score
    reasons: list[str] = []
    if token_score == 1.0 and len(set(_tokens(raw))) > 1:
        score = max(score, 0.93)
        reasons.append("关键词顺序变体")
    if _contains_on_boundary(normalized_raw, normalized_candidate) or _contains_on_boundary(
        normalized_candidate, normalized_raw
    ):
        coverage = min(len(normalized_raw), len(normalized_candidate)) / max(
            len(normalized_raw), len(normalized_candidate)
        )
        score = max(score, 0.70 + 0.18 * coverage)
        reasons.append("名称包含关系")
    if token_score > 0:
        reasons.append("关键词重合")
    if sequence_score >= 0.55:
        reasons.append("拼写相似")
    return min(score, 0.94), tuple(reasons or ("字符串相似",))


def _score_skill(
    raw_skill: str,
    skill: SkillRecord,
    aliases: tuple[str, ...],
    reviewed_skill_id: str | None,
) -> _ScoredSkill:
    score, reasons = _expression_score(raw_skill, skill.skill_name)
    matched_alias = None
    priority = 3 if score == 1.0 else 0
    for alias in aliases:
        alias_score, alias_reasons = _expression_score(raw_skill, alias)
        if alias_score == 1.0:
            alias_score = 0.98
            alias_reasons = ("技能别名精确命中",)
        if alias_score > score:
            score = alias_score
            matched_alias = alias
            reasons = alias_reasons
            priority = 2 if alias_score == 0.98 else priority
    if skill.skill_id == reviewed_skill_id:
        score = 1.0
        priority = 4
        reasons = ("历史人工审核已确认",)
        matched_alias = next(
            (
                alias
                for alias in aliases
                if normalize_skill_expression(alias)
                == normalize_skill_expression(raw_skill)
            ),
            None,
        )
    return _ScoredSkill(skill, score, matched_alias, reasons, priority)


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right) or not left:
        raise CatalogEmbeddingError("embedding vectors have inconsistent dimensions")
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        raise CatalogEmbeddingError("embedding vectors must have non-zero norm")
    score = dot / (left_norm * right_norm)
    if not math.isfinite(score):
        raise CatalogEmbeddingError("embedding cosine must be finite")
    return max(-1.0, min(1.0, score))


def _semantic_scores_chunked(
    *,
    query_vector: tuple[float, ...],
    vectors: tuple[tuple[float, ...], ...],
    scored: list[_ScoredSkill],
    chunk_size: int = SEMANTIC_CHUNK_SIZE,
) -> dict[str, float]:
    """Compute cosine similarities in chunks over the cached skill matrix.

    This keeps memory bounded and avoids an all-at-once score array when the
    active catalog is large (e.g. 10k skills). The result is deterministic
    because chunk boundaries do not affect per-item cosine values.
    """
    semantic_by_id: dict[str, float] = {}
    for start in range(0, len(scored), chunk_size):
        end = min(start + chunk_size, len(scored))
        for item, vector in zip(
            scored[start:end],
            vectors[1 + start : 1 + end],
            strict=True,
        ):
            semantic_by_id[item.skill.skill_id] = _cosine(query_vector, vector)
    return semantic_by_id


def rank_normalization_suggestions(
    *,
    raw_skill: str,
    context: str | None,
    skills: list[SkillRecord],
    aliases: list[SkillAliasRecord],
    reviewed_skill_id: str | None,
    top_k: int,
    embedding: CatalogEmbeddingPort | None,
    lexical_weight: float = LEXICAL_WEIGHT,
    semantic_weight: float = SEMANTIC_WEIGHT,
    lexical_pool_size: int = 40,
    semantic_pool_size: int = SEMANTIC_POOL_SIZE,
    max_semantic_candidates: int = 5000,
) -> tuple[NormalizationSuggestion, ...]:
    """Dual-recall candidate union followed by a unified feature rerank.

    Candidates are ``exact/reviewed mappings UNION lexical Top-N UNION
    semantic Top-N``.  Semantic scores are computed over the full active
    candidate set using the cached skill embedding matrix and chunked cosine
    Top-K, so a correct skill outside the lexical Top-N is no longer invisible
    to the reranker even when the active catalog exceeds 5k skills.
    ``max_semantic_candidates`` is kept for call compatibility; it is no longer
    a hard cutoff that disables semantic retrieval.
    """

    aliases_by_skill: dict[str, list[str]] = {}
    for item in aliases:
        aliases_by_skill.setdefault(item.skill_id, []).append(item.alias)
    scored = [
        _score_skill(
            raw_skill,
            skill,
            tuple(sorted(aliases_by_skill.get(skill.skill_id, ()), key=str.casefold)),
            reviewed_skill_id,
        )
        for skill in skills
        if skill.status == "active"
    ]
    scored.sort(
        key=lambda item: (
            -item.priority,
            -item.lexical_score,
            normalize_skill_expression(item.skill.skill_name),
            item.skill.skill_id,
        )
    )

    semantic_available = False
    semantic_by_id: dict[str, float] = {}
    if embedding is not None and scored:
        query = (raw_skill if not context else f"{raw_skill}\n上下文：{context}")[:4096]
        representations = tuple(
            "；".join(
                part
                for part in (
                    item.skill.skill_name,
                    "、".join(aliases_by_skill.get(item.skill.skill_id, ())),
                    item.skill.category or "",
                )
                if part
            )[:4096]
            for item in scored
        )
        try:
            vectors = embedding.embed((query, *representations))
            if len(vectors) != len(scored) + 1:
                raise CatalogEmbeddingError(
                    "embedding service returned an unexpected vector count"
                )
            query_vector = vectors[0]
            semantic_available = True
            semantic_by_id = _semantic_scores_chunked(
                query_vector=query_vector,
                vectors=vectors,
                scored=scored,
            )
        except CatalogEmbeddingError:
            semantic_available = False

    semantic_top_ids = {
        skill_id
        for skill_id, _score in sorted(
            semantic_by_id.items(),
            key=lambda item: (-item[1], item[0]),
        )[:semantic_pool_size]
    }
    priority_ids = {
        item.skill.skill_id for item in scored if item.priority >= 2
    }
    lexical_top_ids = {
        item.skill.skill_id for item in scored[:lexical_pool_size]
    }
    union_ids = priority_ids | lexical_top_ids | semantic_top_ids

    pooled: list[_ScoredSkill] = []
    for item in scored:
        if item.skill.skill_id not in union_ids:
            continue
        semantic_score = semantic_by_id.get(item.skill.skill_id)
        if semantic_available and semantic_score is not None:
            combined = (
                lexical_weight * item.lexical_score
                + semantic_weight * max(0.0, semantic_score)
            )
            reasons = item.reasons
            if semantic_score >= 0.7 and "语义相似" not in reasons:
                reasons = (*reasons, "语义相似")
            pooled.append(
                replace(
                    item,
                    semantic_score=semantic_score,
                    combined_score=combined,
                    reasons=reasons,
                )
            )
        else:
            pooled.append(
                replace(
                    item,
                    semantic_score=semantic_score,
                    combined_score=(
                        lexical_weight * item.lexical_score
                        if semantic_available
                        else item.lexical_score
                    ),
                )
            )

    pooled.sort(
        key=lambda item: (
            -item.priority,
            -item.combined_score,
            -item.lexical_score,
            -(item.semantic_score if item.semantic_score is not None else -1.0),
            normalize_skill_expression(item.skill.skill_name),
            item.skill.skill_id,
        )
    )
    return tuple(
        NormalizationSuggestion(
            skill_id=item.skill.skill_id,
            skill_name=item.skill.skill_name,
            category=item.skill.category,
            rank=index,
            lexical_score=round(item.lexical_score, 6),
            semantic_score=(
                round(item.semantic_score, 6)
                if item.semantic_score is not None
                else None
            ),
            combined_score=round(item.combined_score, 6),
            matched_alias=item.matched_alias,
            reasons=item.reasons,
            semantic_available=semantic_available,
        )
        for index, item in enumerate(pooled[:top_k], start=1)
    )


__all__ = ["LEXICAL_WEIGHT", "SEMANTIC_WEIGHT", "rank_normalization_suggestions"]
