"""EXP-EMERGE-01 v2.1: stabilized two-stage relation + emergence model.

Version history
    v2 (2026-08-23)  first two-stage implementation; market stats were
                     incorrectly computed per sample instead of per candidate
                     membership, and several relation rules were too loose.
    v2.1 (2026-08-23) method stabilization:
                     - candidate-level market stats now aggregate ALL member
                       JDs of the D5 candidate cluster (cross-company clusters
                       are real in the frozen handoff);
                     - same_or_not_novel uses canonical occupation identity +
                       parent/core retention + responsibility equivalence;
                     - renaming absorbs small non-structural additions instead
                       of flipping to specialization;
                     - specialization requires a single coherent parent with
                       substantial structural weight;
                     - hybridization requires responsibility-side dual-domain
                       evidence, not only skill taxonomy;
                     - unexplained_structural_novelty is gated on reliable
                       novelty in BOTH skill and responsibility dimensions;
                     - formal BGE/embedding-service semantic mode is wired
                       through the repository's embedding-service.v1 contract;
                       lexical fallback remains strictly degraded.

Stage 1 — Occupational Relation
    same_or_not_novel / renaming / specialization / hybridization /
    tool_shift / unexplained_structural_novelty / insufficient_evidence

Stage 2 — Emergence State
    emerging / weak_emerging_signal / not_emerging / insufficient_evidence

Core principle: ``genuine emergence != distance from an old template``.
Only ``structural novelty + temporal persistence/growth + market diffusion``
may output ``emerging``.  A single JD is an observation; the evaluation unit
is the candidate / cluster / lineage / trajectory.  Missing data is reported
as ``unavailable`` / ``insufficient_evidence`` and is never synthesised into
a total score.

The old five-dimension six-class API in :mod:`app.domain.novelty` is kept
unchanged as presentation compatibility.  This module is the new core
inference structure.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.domain.evolution import _bounded

Stage1Relation = Literal[
    "same_or_not_novel",
    "renaming",
    "specialization",
    "hybridization",
    "tool_shift",
    "unexplained_structural_novelty",
    "insufficient_evidence",
]

Stage2State = Literal[
    "emerging",
    "weak_emerging_signal",
    "not_emerging",
    "insufficient_evidence",
]

STAGE1_RELATIONS: tuple[Stage1Relation, ...] = (
    "same_or_not_novel",
    "renaming",
    "specialization",
    "hybridization",
    "tool_shift",
    "unexplained_structural_novelty",
    "insufficient_evidence",
)

STAGE2_STATES: tuple[Stage2State, ...] = (
    "emerging",
    "weak_emerging_signal",
    "not_emerging",
    "insufficient_evidence",
)

DEFAULT_EMERGENCE_V2_CONFIG: dict[str, object] = {
    "policy_version": "emergence-v2.1-stabilization-20260823",
    "name": {
        "canonical_match_floor": 0.60,
        "canonical_token_overlap_min": 0.25,
        "same_name_similarity_min": 0.55,
        "renaming_name_similarity_max": 0.60,
    },
    "skill": {
        "peer_core_support_min": 0.35,
        "peer_responsibility_support_min": 2,
        "parent_core_retained_min": 0.60,
        "same_core_retained_min": 0.70,
        "renaming_core_retained_min": 0.65,
        "structural_weight_min": 0.20,
        "structural_skill_count_min": 2,
        "subdomain_added_skills_min": 2,
        "specialization_added_domain_count": 1,
        "hybridization_min_added_domains": 2,
        "hybridization_min_added_skills_per_domain": 2,
        "structural_skill_change_min": 2,
        "tool_shift_tool_change_ratio_min": 0.40,
    },
    "responsibility": {
        "retained_similarity_min": 0.72,
        "transformed_similarity_min": 0.45,
        "same_resp_retained_min": 0.50,
        "renaming_resp_retained_min": 0.50,
        "structural_resp_change_min": 2,
        "degraded_structural_resp_change_min": 4,
        "hybridization_min_domains_in_resp": 2,
    },
    "market": {
        "min_windows_for_growth": 2,
        "min_enterprise_diffusion": 2,
        "max_source_concentration_share": 0.80,
        "source_concentrated_confidence_multiplier": 0.50,
    },
    "emergence": {
        "structural_relations": ("unexplained_structural_novelty", "hybridization"),
    },
}


def _effective_config(config: Mapping[str, object] | None) -> dict[str, object]:
    merged = {**DEFAULT_EMERGENCE_V2_CONFIG, **(config or {})}
    for section in ("name", "skill", "responsibility", "market", "emergence"):
        merged[section] = {
            **dict(DEFAULT_EMERGENCE_V2_CONFIG[section]),
            **dict(merged.get(section) or {}),
        }
    return merged


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def policy_config_version(config: Mapping[str, object] | None = None) -> str:
    from hashlib import sha256

    merged = _effective_config(config)
    digest = sha256(_canonical_json(merged).encode("utf-8")).hexdigest()[:16]
    return f"{merged['policy_version']}/sha256:{digest}"


# ── Taxonomy assets ──


@dataclass(frozen=True)
class SkillInfo:
    """Normalised skill with formal taxonomy classification."""

    raw: str
    skill_id: str
    canonical_name: str
    category_code: str | None
    subcategory_code: str | None
    domains: frozenset[str]
    resolved: bool = True

    def to_dict(self) -> Mapping[str, object]:
        return {
            "raw": self.raw,
            "skill_id": self.skill_id,
            "canonical_name": self.canonical_name,
            "category_code": self.category_code,
            "subcategory_code": self.subcategory_code,
            "domains": sorted(self.domains),
            "resolved": self.resolved,
        }


@dataclass(frozen=True)
class SkillIndex:
    """Inverted index over the formal skill taxonomy catalog + extraction map."""

    raw_map: Mapping[str, SkillInfo]
    canonical_map: Mapping[str, tuple[str, ...]]
    catalog_version: str

    def resolve(self, raw: str) -> SkillInfo:
        key = unicodedata.normalize("NFKC", raw).strip()
        if key in self.raw_map:
            return self.raw_map[key]
        folded = key.casefold()
        hits = self.canonical_map.get(folded, ())
        if len(hits) == 1:
            # deterministic canonical-name lookup for raw names that only exist
            # as canonical entries
            for info in self.raw_map.values():
                if info.skill_id == hits[0]:
                    return info
        return SkillInfo(
            raw=raw,
            skill_id=f"UNRESOLVED:{folded}",
            canonical_name=raw,
            category_code=None,
            subcategory_code=None,
            domains=frozenset(),
            resolved=False,
        )

    def resolve_many(self, raws: Sequence[str]) -> tuple[SkillInfo, ...]:
        return tuple(self.resolve(raw) for raw in raws)

    def domain_keywords(self) -> Mapping[str, frozenset[str]]:
        """Domain vocabulary built from the reviewed taxonomy (canonical+raw)."""
        keywords: dict[str, set[str]] = {}
        for info in self.raw_map.values():
            for domain in info.domains:
                bucket = keywords.setdefault(domain, set())
                for value in (info.canonical_name, info.raw):
                    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
                    if len(normalized) >= 2:
                        bucket.add(normalized)
        return {domain: frozenset(values) for domain, values in keywords.items()}


def build_skill_index(
    catalog: Mapping[str, object],
    normalization_map: Mapping[str, object],
) -> SkillIndex:
    """Build a raw-skill -> normalised skill index from frozen assets.

    ``catalog`` is ``skill_taxonomy_catalog.v1.json``; ``normalization_map``
    is the ``extraction.normalization-map.v2`` content parsed from
    ``phase0_contract_baseline.json`` (raw skill -> skill_id/category).
    Domain classification comes from the reviewed catalog only.
    """
    domains_by_id: dict[str, frozenset[str]] = {}
    for skill_id, entry in (catalog.get("skills") or {}).items():
        domains = frozenset(
            str(rel["code"])
            for rel in (entry.get("classifications") or [])
            if rel.get("facet") == "domain" and rel.get("is_primary")
        )
        domains_by_id[skill_id] = domains
    raw_map: dict[str, SkillInfo] = {}
    canonical_map: dict[str, list[str]] = {}
    for raw, entry in (normalization_map.get("skills") or {}).items():
        skill_id = str(entry.get("skill_id") or "")
        canonical = str(entry.get("canonical_name") or raw)
        if not skill_id:
            continue
        raw_map[unicodedata.normalize("NFKC", raw).strip()] = SkillInfo(
            raw=raw,
            skill_id=skill_id,
            canonical_name=canonical,
            category_code=entry.get("category_code") and str(entry["category_code"]),
            subcategory_code=(
                entry.get("subcategory_code") and str(entry["subcategory_code"])
            ),
            domains=domains_by_id.get(skill_id, frozenset()),
        )
        canonical_map.setdefault(canonical.casefold(), []).append(skill_id)
    return SkillIndex(
        raw_map=raw_map,
        canonical_map={key: tuple(values) for key, values in canonical_map.items()},
        catalog_version=str(catalog.get("catalog_version") or "skill-taxonomy-catalog.v1"),
    )


def _responsibility_mentions_keyword(text: str, keyword: str) -> bool:
    if keyword.isascii():
        return (
            f" {keyword} " in f" {text} "
            or text.startswith(f"{keyword} ")
            or text.endswith(f" {keyword}")
            or text == keyword
        )
    return keyword in text


def responsibility_domain_evidence(
    responsibilities: Sequence[str],
    domain_keywords: Mapping[str, frozenset[str]],
    domains: Sequence[str],
) -> Mapping[str, object]:
    """Check which claimed taxonomy domains are actually evidenced in the JD text."""
    result: dict[str, object] = {}
    for domain in domains:
        keywords = domain_keywords.get(domain, frozenset())
        hits: list[dict[str, object]] = []
        for responsibility in responsibilities:
            text = unicodedata.normalize("NFKC", responsibility).casefold()
            found = sorted(
                keyword
                for keyword in keywords
                if _responsibility_mentions_keyword(text, keyword)
            )
            if found:
                hits.append(
                    {
                        "responsibility": responsibility,
                        "keywords": found[:5],
                    }
                )
        result[domain] = {
            "present": bool(hits),
            "hit_count": len(hits),
            "hits": hits,
        }
    return result


@dataclass(frozen=True)
class PositionInfo:
    code: str
    name: str
    family_code: str
    family_name: str
    definition: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class PositionIndex:
    positions: Mapping[str, PositionInfo]
    taxonomy_version: str

    def get(self, code: str) -> PositionInfo | None:
        return self.positions.get(code)


def build_position_index(catalog: Mapping[str, object]) -> PositionIndex:
    positions: dict[str, PositionInfo] = {}
    for entry in catalog.get("positions") or ():
        code = str(entry.get("position_code") or "")
        if not code:
            continue
        positions[code] = PositionInfo(
            code=code,
            name=str(entry.get("position_name") or ""),
            family_code=str(entry.get("family_code") or ""),
            family_name=str(entry.get("family_name") or ""),
            definition=str(entry.get("definition") or ""),
            aliases=tuple(str(item) for item in (entry.get("aliases") or ())),
        )
    return PositionIndex(
        positions=positions,
        taxonomy_version=str(catalog.get("taxonomy_version") or "position-taxonomy.v3.0.0"),
    )


# ── Dimension evidence ──


@dataclass(frozen=True)
class DimensionEvidence:
    dimension: str
    available: bool
    score: float | None
    confidence: float | None
    reason: str
    evidence_refs: tuple[str, ...] = ()
    degraded: bool = False
    components: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Mapping[str, object]:
        return {
            "dimension": self.dimension,
            "available": self.available,
            "score": round(float(self.score), 6) if self.score is not None else None,
            "confidence": (
                round(float(self.confidence), 6)
                if self.confidence is not None
                else None
            ),
            "reason": self.reason,
            "evidence_refs": list(self.evidence_refs),
            "degraded": self.degraded,
            "components": {
                key: (
                    [round(float(v), 6) for v in value]
                    if isinstance(value, list)
                    and value
                    and all(isinstance(v, (int, float)) for v in value)
                    else value
                )
                for key, value in self.components.items()
            },
        }


@dataclass(frozen=True)
class RelationClassification:
    relation: Stage1Relation
    confidence: float
    reason: str
    components: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Mapping[str, object]:
        return {
            "relation": self.relation,
            "confidence": round(float(self.confidence), 6),
            "reason": self.reason,
            "components": self.components,
        }


@dataclass(frozen=True)
class EmergenceClassification:
    state: Stage2State
    confidence: float
    reason: str
    components: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Mapping[str, object]:
        return {
            "state": self.state,
            "confidence": round(float(self.confidence), 6),
            "reason": self.reason,
            "components": self.components,
        }


# ── Text similarity helpers (deterministic, taxonomy-first) ──

_CJK_RE = r"[㐀-䶿一-鿿豈-﫿]"
_RUN_RE = re.compile(rf"[0-9a-z]+|{_CJK_RE}+")


def _cjk_bigrams(run: str) -> set[str]:
    if len(run) <= 1:
        return {run}
    return {run[i : i + 2] for i in range(len(run) - 1)}


def _text_tokens(text: str) -> frozenset[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens: set[str] = set()
    for run in _RUN_RE.findall(normalized):
        if run[0].isascii():
            tokens.add(run)
        else:
            tokens.update(_cjk_bigrams(run))
    return frozenset(tokens)


def _jaccard_similarity(left: frozenset[str], right: frozenset[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _text_similarity(left: str, right: str) -> float:
    return _jaccard_similarity(_text_tokens(left), _text_tokens(right))


def _canonical_token_overlap(title: str, canonical_title: str) -> float:
    """Fraction of canonical title tokens contained in the candidate title."""
    canonical_tokens = _text_tokens(canonical_title)
    if not canonical_tokens:
        return 0.0
    title_tokens = _text_tokens(title)
    return len(canonical_tokens & title_tokens) / len(canonical_tokens)


def _edit_distance_ratio(left: str, right: str) -> float:
    """Normalised Levenshtein ratio; used only as a fallback feature."""
    if not left and not right:
        return 0.0
    if not left or not right:
        return 1.0
    len_l, len_r = len(left), len(right)
    prev = list(range(len_r + 1))
    curr = [0] * (len_r + 1)
    for i in range(1, len_l + 1):
        curr[0] = i
        for j in range(1, len_r + 1):
            cost = 0 if left[i - 1] == right[j - 1] else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev, curr = curr, prev
    return prev[len_r] / max(len_l, len_r)


# ── 1. Name novelty ──


def score_name_novelty_v2(
    current_title: str,
    *,
    baseline_titles: Sequence[str] = (),
    canonical_title: str | None = None,
    taxonomy_version: str = "",
    config: Mapping[str, object] | None = None,
) -> DimensionEvidence:
    """Taxonomy-first name novelty.

    Levenshtein is only a fallback feature.  The primary signal is semantic
    title similarity against the candidate's own history and the canonical
    position title; a canonical-position match means ``renaming`` territory,
    not structural novelty.
    """
    effective = _effective_config(config)
    name_cfg = dict(effective["name"])
    canonical_floor = float(name_cfg["canonical_match_floor"])
    targets = [title for title in baseline_titles if title]
    if canonical_title:
        targets.append(canonical_title)
    if not targets:
        return DimensionEvidence(
            dimension="name_novelty",
            available=False,
            score=None,
            confidence=None,
            reason="NO_DATA: no own-history or peer/canonical baseline title",
        )
    semantic_sims = [_text_similarity(current_title, target) for target in targets]
    best_semantic = max(semantic_sims, default=0.0)
    canonical_sim = (
        _text_similarity(current_title, canonical_title)
        if canonical_title
        else None
    )
    edit_distances = [_edit_distance_ratio(current_title, target) for target in targets]
    min_edit = min(edit_distances, default=1.0)
    # canonical-position match: the occupation is known; naming drift is low novelty
    if canonical_sim is not None and canonical_sim >= canonical_floor:
        score = _bounded(0.15 * (1.0 - canonical_sim))
        reason = (
            f"canonical_position_match={canonical_sim:.3f} "
            f"(occupation known; structural name novelty low)"
        )
    else:
        score = _bounded(1.0 - best_semantic)
        reason = f"semantic_title_similarity={best_semantic:.3f}"
    return DimensionEvidence(
        dimension="name_novelty",
        available=True,
        score=score,
        confidence=_bounded(0.5 + 0.5 * (1.0 - score)),
        reason=reason,
        components={
            "current_title": current_title,
            "best_semantic_title_similarity": round(best_semantic, 6),
            "canonical_title_similarity": (
                round(canonical_sim, 6) if canonical_sim is not None else None
            ),
            "canonical_token_overlap": (
                round(_canonical_token_overlap(current_title, canonical_title), 6)
                if canonical_title
                else 0.0
            ),
            "fallback_edit_distance": round(min_edit, 6),
            "canonical_title": canonical_title or "",
            "taxonomy_version": taxonomy_version,
        },
    )


# ── 2. Skill-combination novelty ──


def score_skill_combination_novelty_v2(
    current_skills: Sequence[SkillInfo],
    *,
    before_skills: Sequence[SkillInfo] | None = None,
    peer_skill_sets: Sequence[Sequence[SkillInfo]] = (),
    config: Mapping[str, object] | None = None,
) -> DimensionEvidence:
    """Normalised skill-ID + taxonomy-domain skill combination novelty."""
    if before_skills is None:
        return DimensionEvidence(
            dimension="skill_combination_novelty",
            available=False,
            score=None,
            confidence=None,
            reason="NO_DATA: no before/peer baseline skills",
        )
    cur_ids = frozenset(skill.skill_id for skill in current_skills)
    before_ids = frozenset(skill.skill_id for skill in before_skills)
    retained = sorted(cur_ids & before_ids)
    added_ids = sorted(cur_ids - before_ids)
    removed_ids = sorted(before_ids - cur_ids)
    retained_ratio = len(retained) / len(before_ids) if before_ids else 0.0
    cur_domains = {
        domain for skill in current_skills for domain in skill.domains
    }
    before_domains = {domain for skill in before_skills for domain in skill.domains}
    added_domains = sorted(cur_domains - before_domains)
    cur_subcategories = {
        skill.subcategory_code
        for skill in current_skills
        if skill.subcategory_code
    }
    before_subcategories = {
        skill.subcategory_code
        for skill in before_skills
        if skill.subcategory_code
    }
    added_subcategories = sorted(cur_subcategories - before_subcategories)
    # structural subdomains: additions in knowledge/methodology/database etc.,
    # not pure tool/framework/language/platform replacements
    non_structural_categories = {
        "programming_language",
        "tool",
        "framework",
        "platform_service",
        "library_sdk",
        "middleware_runtime",
        "protocol_standard",
    }
    structural_added_subdomains = sorted(
        {
            skill.subcategory_code
            for skill in current_skills
            if skill.subcategory_code in added_subcategories
            and skill.category_code not in non_structural_categories
        }
    )
    added_structural_skills = sorted(
        skill.skill_id
        for skill in current_skills
        if skill.skill_id in added_ids
        and skill.category_code not in non_structural_categories
    )
    added_structural_skill_domains = sorted(
        {
            domain
            for skill in current_skills
            if skill.skill_id in added_ids
            and skill.category_code not in non_structural_categories
            for domain in skill.domains
        }
    )
    structural_weight = len(added_structural_skills) / max(
        len(cur_ids | before_ids), 1
    )
    subdomain_counts: dict[str, int] = {}
    for skill in current_skills:
        if (
            skill.skill_id in added_ids
            and skill.category_code not in non_structural_categories
            and skill.subcategory_code
        ):
            subdomain_counts[skill.subcategory_code] = (
                subdomain_counts.get(skill.subcategory_code, 0) + 1
            )
    # parent-core retention against the *relevant* baseline subset (same
    # subcategory/domain as the candidate), not the full occupation union
    # parent-core baseline = the occupation subset whose subcategory matches
    # the candidate; only when no subcategory hits exist fall back to the
    # coarse domain-level overlap.
    relevant_before = [
        skill
        for skill in before_skills
        if skill.subcategory_code in cur_subcategories
    ]
    if not relevant_before:
        relevant_before = [
            skill
            for skill in before_skills
            if skill.domains & cur_domains
        ]
    relevant_before_ids = frozenset(skill.skill_id for skill in relevant_before)
    relevant_retained_ratio = (
        len(cur_ids & relevant_before_ids) / len(relevant_before_ids)
        if relevant_before_ids
        else 0.0
    )
    changed_count = len(added_ids) + len(removed_ids)
    change_ratio = changed_count / max(len(cur_ids | before_ids), 1)

    peer_ids = [frozenset(skill.skill_id for skill in peer_set) for peer_set in peer_skill_sets]
    peer_rarity_scores: list[float] = []
    for skill_id in added_ids:
        share = (
            sum(1 for peer in peer_ids if skill_id in peer) / len(peer_ids)
            if peer_ids
            else 0.0
        )
        peer_rarity_scores.append(1.0 - share)
    peer_rarity = sum(peer_rarity_scores) / len(peer_rarity_scores) if peer_rarity_scores else 0.0

    co_occurrence_scores: list[float] = []
    for retained_id in retained:
        for added_id in added_ids:
            pair_seen = sum(
                1 for peer in peer_ids if retained_id in peer and added_id in peer
            )
            share = pair_seen / len(peer_ids) if peer_ids else 0.0
            co_occurrence_scores.append(1.0 - share)
    co_occurrence_novelty = (
        sum(co_occurrence_scores) / len(co_occurrence_scores)
        if co_occurrence_scores
        else 0.0
    )

    domain_added_ratio = min(1.0, len(added_domains) / 2.0)
    score = _bounded(
        0.35 * change_ratio
        + 0.30 * domain_added_ratio
        + 0.20 * peer_rarity
        + 0.15 * co_occurrence_novelty
    )
    return DimensionEvidence(
        dimension="skill_combination_novelty",
        available=True,
        score=score,
        confidence=_bounded(0.5 + 0.5 * score),
        reason=(
            f"retained={len(retained)} added={len(added_ids)} "
            f"removed={len(removed_ids)} added_domains={len(added_domains)}"
        ),
        components={
            "retained_core_skills": retained,
            "added_skills": added_ids,
            "removed_skills": removed_ids,
            "added_domains": added_domains,
            "added_subcategories": added_subcategories,
            "structural_added_subdomains": structural_added_subdomains,
            "added_structural_skills": added_structural_skills,
            "added_structural_skill_domains": added_structural_skill_domains,
            "structural_weight": round(structural_weight, 6),
            "added_skills_per_subdomain": dict(subdomain_counts),
            "retained_core_ratio": round(retained_ratio, 6),
            "relevant_baseline_retained_ratio": round(relevant_retained_ratio, 6),
            "peer_rarity": round(peer_rarity, 6),
            "co_occurrence_novelty": round(co_occurrence_novelty, 6),
            "change_ratio": round(change_ratio, 6),
            "current_skill_infos": [skill.to_dict() for skill in current_skills],
            "before_skill_ids": sorted(before_ids),
        },
    )


# ── 3. Responsibility structure ──


class SemanticTextEncoder:
    """Protocol for responsibility alignment encoders."""

    mode = "base"

    def similarity(self, left: str, right: str) -> float:
        raise NotImplementedError


class LexicalFallbackEncoder(SemanticTextEncoder):
    """Deterministic token/bigram fallback; always marked degraded."""

    mode = "lexical_fallback"

    def similarity(self, left: str, right: str) -> float:
        return _text_similarity(left, right)


class EmbeddingSemanticEncoder(SemanticTextEncoder):
    """Embedding-based alignment; enabled only when an encoder is available."""

    mode = "embedding"

    def __init__(self, embed_fn: object):
        self._embed = embed_fn

    def similarity(self, left: str, right: str) -> float:
        left_vec = self._embed(left)  # type: ignore[operator]
        right_vec = self._embed(right)  # type: ignore[operator]
        if not left_vec or not right_vec or len(left_vec) != len(right_vec):
            return 0.0
        norm_l = sum(v * v for v in left_vec) ** 0.5 or 1.0
        norm_r = sum(v * v for v in right_vec) ** 0.5 or 1.0
        dot = sum(a * b for a, b in zip(left_vec, right_vec, strict=False))
        return max(0.0, min(1.0, dot / (norm_l * norm_r)))


def score_responsibility_structure_v2(
    current_responsibilities: Sequence[str],
    *,
    before_responsibilities: Sequence[str] | None = None,
    encoder: SemanticTextEncoder | None = None,
    config: Mapping[str, object] | None = None,
) -> DimensionEvidence:
    """Responsibility alignment: retained / added / removed / transformed."""
    if before_responsibilities is None:
        return DimensionEvidence(
            dimension="responsibility_structure_novelty",
            available=False,
            score=None,
            confidence=None,
            reason="NO_DATA: no before/peer baseline responsibilities",
        )
    effective = _effective_config(config)
    resp_cfg = dict(effective["responsibility"])
    retained_min = float(resp_cfg["retained_similarity_min"])
    transformed_min = float(resp_cfg["transformed_similarity_min"])
    encoder = encoder or LexicalFallbackEncoder()
    degraded = encoder.mode != "embedding"

    retained: list[str] = []
    transformed: list[str] = []
    added: list[str] = []
    pair_sims: list[dict[str, object]] = []
    for current in current_responsibilities:
        sims = [encoder.similarity(current, before) for before in before_responsibilities]
        best = max(sims, default=0.0)
        pair_sims.append(
            {
                "current": current,
                "best_similarity": round(best, 6),
                "best_before": (
                    before_responsibilities[sims.index(best)]
                    if sims
                    else ""
                ),
            }
        )
        if best >= retained_min:
            retained.append(current)
        elif best >= transformed_min:
            transformed.append(current)
        else:
            added.append(current)
    removed = [
        before
        for before in before_responsibilities
        if not any(
            encoder.similarity(current, before) >= retained_min
            for current in current_responsibilities
        )
    ]
    union_size = max(
        len(set(current_responsibilities) | set(before_responsibilities)),
        1,
    )
    changed = len(added) + len(transformed) + len(removed)
    score = _bounded(changed / union_size)
    return DimensionEvidence(
        dimension="responsibility_structure_novelty",
        available=True,
        score=score,
        confidence=_bounded(0.5 + 0.5 * score) if not degraded else _bounded(0.35 + 0.35 * score),
        reason=(
            f"retained={len(retained)} transformed={len(transformed)} "
            f"added={len(added)} removed={len(removed)}"
        ),
        degraded=degraded,
        components={
            "retained_responsibilities": retained,
            "transformed_responsibilities": transformed,
            "added_responsibilities": added,
            "removed_responsibilities": removed,
            "pairwise_alignment": pair_sims,
            "semantic_mode": encoder.mode,
            "cross_domain_composition": None,
            "cross_domain_composition_available": False,
        },
    )


# ── 4. Industry scenario ──


def score_industry_scenario_v2(
    industry_codes: Sequence[str] = (),
    *,
    historic_industry_codes: Sequence[str] = (),
) -> DimensionEvidence:
    """Industry scenario novelty; never synthesises a default score."""
    current = frozenset(industry_codes)
    historic = frozenset(historic_industry_codes)
    if not current:
        return DimensionEvidence(
            dimension="industry_scenario_novelty",
            available=False,
            score=None,
            confidence=None,
            reason="NO_DATA: no industry codes available",
        )
    if not historic:
        return DimensionEvidence(
            dimension="industry_scenario_novelty",
            available=False,
            score=None,
            confidence=None,
            reason="NO_DATA: no historical industry baseline",
            components={
                "total_current_codes": len(current),
                "historic_code_count": 0,
            },
        )
    new_codes = sorted(current - historic)
    ratio = len(new_codes) / len(current)
    return DimensionEvidence(
        dimension="industry_scenario_novelty",
        available=True,
        score=_bounded(ratio),
        confidence=_bounded(0.5 + 0.5 * ratio),
        reason=f"new_industry_codes={len(new_codes)}/{len(current)}",
        components={
            "new_industry_codes": new_codes,
            "total_current_codes": len(current),
            "historic_code_count": len(historic),
        },
    )


# ── 5. Market behavior (candidate-level) ──


@dataclass(frozen=True)
class CandidateMarketStats:
    candidate_id: str
    window_counts: tuple[tuple[str, int], ...]
    enterprises: tuple[str, ...]
    regions: tuple[str, ...]
    sources: tuple[str, ...]

    def to_dict(self) -> Mapping[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "window_counts": [
                {"window_id": window_id, "jd_count": count}
                for window_id, count in self.window_counts
            ],
            "enterprises": list(self.enterprises),
            "regions": list(self.regions),
            "sources": list(self.sources),
        }


def score_market_behavior_v2(
    stats: CandidateMarketStats,
    *,
    window_order: Sequence[str] = (),
    config: Mapping[str, object] | None = None,
) -> DimensionEvidence:
    """Candidate/cluster-level market evidence with source concentration handling."""
    effective = _effective_config(config)
    mkt_cfg = dict(effective["market"])
    min_windows = int(mkt_cfg["min_windows_for_growth"])
    min_enterprises = int(mkt_cfg["min_enterprise_diffusion"])
    max_conc = float(mkt_cfg["max_source_concentration_share"])
    order = tuple(window_order)
    ordered_counts = sorted(
        stats.window_counts,
        key=lambda item: (
            order.index(item[0]) if item[0] in order else len(order),
            item[0],
        ),
    )
    valid = [(window_id, count) for window_id, count in ordered_counts if count > 0]
    n_windows = len(valid)
    jd_count = sum(count for _, count in valid)
    enterprises = set(stats.enterprises)
    regions = set(stats.regions)
    sources = list(stats.sources)
    source_counts: dict[str, int] = {}
    for source in sources:
        source_counts[source] = source_counts.get(source, 0) + 1
    source_diversity = len(source_counts)
    source_total = sum(source_counts.values()) or 1
    max_source_share = max(source_counts.values()) / source_total if source_counts else 0.0
    hhi = sum((count / source_total) ** 2 for count in source_counts.values())
    source_concentrated = (
        source_diversity == 0
        or source_diversity < 2
        or max_source_share > max_conc
        or hhi > 0.64
    )
    if jd_count == 0:
        return DimensionEvidence(
            dimension="market_behavior_novelty",
            available=False,
            score=None,
            confidence=None,
            reason="NO_DATA: no market observations",
        )

    growth_available = n_windows >= min_windows
    growth_score = 0.0
    growth_factor = 1.0
    persistence = 0.0
    enterprise_growth = 0
    first_appearance = False
    survival = False
    if growth_available:
        midpoint = n_windows // 2
        first_half = sum(count for _, count in valid[:midpoint])
        second_half = sum(count for _, count in valid[midpoint:])
        growth_score = _bounded(
            (math.log1p(second_half) - math.log1p(first_half))
            / max(math.log1p(max(jd_count, 1)), 0.01)
        )
        growth_factor = (
            (second_half + 1.0) / (first_half + 1.0)
            if first_half >= 0
            else 1.0
        )
        persistence = n_windows / max(min_windows, 1)
        first_half_enterprises = {
            ent
            for window_id, _ in valid[:midpoint]
            for ent in stats.enterprises
        }
        second_half_enterprises = {
            ent
            for window_id, _ in valid[midpoint:]
            for ent in stats.enterprises
        }
        enterprise_growth = len(second_half_enterprises - first_half_enterprises)
    first_appearance = n_windows == 1 and bool(valid)
    survival = n_windows >= 1 and (not order or valid[-1][0] == order[-1])

    enterprise_score = _bounded(len(enterprises) / 10.0)
    geo_score = _bounded(len(regions) / 5.0)
    score = _bounded(
        0.40 * growth_score
        + 0.30 * enterprise_score
        + 0.20 * geo_score
        + 0.10 * persistence
    )
    confidence = 0.80 if growth_available else 0.35
    if source_concentrated:
        confidence = _bounded(
            confidence * float(mkt_cfg["source_concentrated_confidence_multiplier"])
        )
    return DimensionEvidence(
        dimension="market_behavior_novelty",
        available=True,
        score=score,
        confidence=confidence,
        reason=(
            f"windows={n_windows}, jds={jd_count}, enterprises={len(enterprises)}, "
            f"growth_available={growth_available}"
        ),
        components={
            "jd_count": jd_count,
            "enterprise_count": len(enterprises),
            "geographic_spread": len(regions),
            "source_diversity": source_diversity,
            "max_source_share": round(max_source_share, 6),
            "source_hhi": round(hhi, 6),
            "source_concentrated": source_concentrated,
            "growth_available": growth_available,
            "growth_factor": round(growth_factor, 6),
            "persistence": round(persistence, 6),
            "first_appearance": first_appearance,
            "survival": survival,
            "enterprise_growth": enterprise_growth,
            "multi_enterprise": len(enterprises) >= min_enterprises,
            "window_counts": [
                {"window_id": window_id, "jd_count": count}
                for window_id, count in ordered_counts
            ],
        },
    )


# ── Stage 1 classification ──


def classify_relation_v2(
    *,
    name_dim: DimensionEvidence,
    skill_dim: DimensionEvidence,
    resp_dim: DimensionEvidence,
    baseline_source: str,
    taxonomy_available: bool,
    domain_evidence: Mapping[str, object] | None = None,
    config: Mapping[str, object] | None = None,
) -> RelationClassification:
    """v2.1 Stage 1 occupational relation classification.

    Stabilization rules:
    - same_or_not_novel: canonical occupation identity + parent/core retention
      + responsibility equivalence; stable mature roles land here reliably.
    - renaming: identity consistent, core retained, but added structure is too
      small to be specialization/hybridization; marketing/title wording change.
    - specialization: single coherent parent, parent core mostly retained, the
      added subdomain carries enough structural weight (never a single added
      skill).
    - hybridization: skill-side dual-domain structure AND responsibility-side
      dual-domain evidence; taxonomy-only cross-domain is not enough.
    - unexplained_structural_novelty: parent cannot explain + reliable novelty
      in BOTH skill and responsibility dimensions + sufficient evidence.
      Single-dimension changes downgrade to other relations / insufficient.
    """
    effective = _effective_config(config)
    name_cfg = dict(effective["name"])
    skill_cfg = dict(effective["skill"])
    resp_cfg = dict(effective["responsibility"])
    canonical_token_min = float(name_cfg["canonical_token_overlap_min"])
    same_core_min = float(skill_cfg["same_core_retained_min"])
    renaming_core_min = float(skill_cfg["renaming_core_retained_min"])
    parent_core_min = float(skill_cfg["parent_core_retained_min"])
    structural_weight_min = float(skill_cfg["structural_weight_min"])
    structural_skill_min = int(skill_cfg["structural_skill_count_min"])
    subdomain_skills_min = int(skill_cfg["subdomain_added_skills_min"])
    hybrid_domains = int(skill_cfg["hybridization_min_added_domains"])
    hybrid_per_domain = int(skill_cfg["hybridization_min_added_skills_per_domain"])
    tool_change_min = float(skill_cfg["tool_shift_tool_change_ratio_min"])
    renaming_resp_min = float(resp_cfg["renaming_resp_retained_min"])
    structural_resp_min = int(resp_cfg["structural_resp_change_min"])
    resp_domains_min = int(resp_cfg["hybridization_min_domains_in_resp"])

    if baseline_source == "none":
        return RelationClassification(
            relation="insufficient_evidence",
            confidence=0.0,
            reason="NO_BASELINE: no own history and no peer/taxonomy baseline",
        )
    name_sim = 1.0 - (name_dim.score or 0.0)
    canonical_sim = float(name_dim.components.get("canonical_title_similarity") or 0.0)
    canonical_token_overlap = float(
        name_dim.components.get("canonical_token_overlap") or 0.0
    )
    skill_retained = float(skill_dim.components.get("retained_core_ratio", 0.0))
    relevant_retained = float(
        skill_dim.components.get("relevant_baseline_retained_ratio", 0.0)
    )
    added_skills = list(skill_dim.components.get("added_skills", ()) or ())
    removed_skills = list(skill_dim.components.get("removed_skills", ()) or ())
    added_domains = frozenset(skill_dim.components.get("added_domains", ()) or ())
    structural_added_subdomains = frozenset(
        skill_dim.components.get("structural_added_subdomains", ()) or ()
    )
    added_structural_skills = list(
        skill_dim.components.get("added_structural_skills", ()) or ()
    )
    structural_weight = float(skill_dim.components.get("structural_weight", 0.0))
    added_by_subdomain = dict(
        skill_dim.components.get("added_skills_per_subdomain", {}) or {}
    )
    added_structural_domains = frozenset(
        skill_dim.components.get("added_structural_skill_domains", ()) or ()
    )
    resp_retained_count = len(
        resp_dim.components.get("retained_responsibilities", ()) or ()
    )
    resp_added_count = len(resp_dim.components.get("added_responsibilities", ()) or ())
    resp_transformed_count = len(
        resp_dim.components.get("transformed_responsibilities", ()) or ()
    )
    resp_removed_count = len(
        resp_dim.components.get("removed_responsibilities", ()) or ()
    )
    resp_total = (
        resp_retained_count
        + resp_added_count
        + resp_transformed_count
        + resp_removed_count
    )
    resp_retained = resp_retained_count / resp_total if resp_total else 0.0
    resp_changed = resp_added_count + resp_transformed_count + resp_removed_count

    resp_soft = resp_dim.degraded
    core_retained = max(skill_retained, relevant_retained)

    components: dict[str, object] = {
        "name_semantic_similarity": round(name_sim, 6),
        "canonical_title_similarity": round(canonical_sim, 6),
        "canonical_token_overlap": round(canonical_token_overlap, 6),
        "skill_retained_ratio": round(skill_retained, 6),
        "relevant_baseline_retained_ratio": round(relevant_retained, 6),
        "resp_retained_ratio": round(resp_retained, 6),
        "resp_changed_count": resp_changed,
        "added_domains": sorted(added_domains),
        "structural_added_subdomains": sorted(structural_added_subdomains),
        "added_structural_skills": added_structural_skills,
        "structural_weight": round(structural_weight, 6),
        "responsibility_mode": (
            "embedding" if not resp_soft else "lexical_fallback_degraded"
        ),
        "baseline_source": baseline_source,
    }

    if (
        not taxonomy_available
        and (added_domains or structural_added_subdomains)
        and len(added_structural_skills) >= structural_skill_min
    ):
        return RelationClassification(
            relation="insufficient_evidence",
            confidence=0.2,
            reason="TAXONOMY_UNAVAILABLE: cannot attribute structural change to a formal domain",
            components=components,
        )

    # ── tool_shift ──
    # checked before same_or_not_novel: a tool/framework replacement is more
    # specific than a stable/renaming label.
    resp_ok_rename = resp_soft or resp_retained >= renaming_resp_min
    tool_change_ratio = _tool_change_ratio(
        skill_dim,
        added_skills + removed_skills,
    )
    if (
        core_retained >= renaming_core_min
        and resp_ok_rename
        and not added_structural_skills
        and tool_change_ratio >= tool_change_min
    ):
        return RelationClassification(
            relation="tool_shift",
            confidence=_bounded(tool_change_ratio * (0.85 if resp_soft else 1.0)),
            reason="tool_shift: core stable, tool/framework replacements",
            components={**components, "tool_change_ratio": round(tool_change_ratio, 6)},
        )

    # ── same_or_not_novel ──
    # canonical identity: title core overlaps the canonical occupation, or the
    # semantic title similarity to the canonical title is high; no marketing
    # wording signal; core retention; responsibility equivalence.  Non-
    # structural skill additions (e.g., an extra database tool) do not block
    # stability; structural additions do.
    naming_marker_present = _title_has_naming_marker(name_dim)
    canonical_identity = (
        canonical_sim >= 0.70
        or canonical_token_overlap >= 0.50
        or name_sim >= 0.60
    )
    resp_ok_same = (
        (not resp_soft and resp_retained >= 0.50)
        or (resp_soft and core_retained >= same_core_min)
    )
    if (
        canonical_identity
        and not naming_marker_present
        and core_retained >= same_core_min
        and resp_ok_same
        and not added_structural_skills
    ):
        return RelationClassification(
            relation="same_or_not_novel",
            confidence=_bounded(
                (0.55 + 0.45 * core_retained) * (0.85 if resp_soft else 1.0)
            ),
            reason=(
                "stable: canonical occupation identity + parent/core retention "
                "+ no structural addition"
            ),
            components=components,
        )

    # ── renaming ──
    # occupation identity basically consistent; core skills/responsibilities
    # retained; added structure insufficient for specialization/hybridization;
    # title/marketing wording changed.
    identity_consistent = (
        canonical_sim >= 0.45
        or canonical_token_overlap >= canonical_token_min
        or name_sim >= 0.35
    )
    added_structure_insufficient = (
        structural_weight < structural_weight_min
        and len(added_structural_skills) < structural_skill_min
        and len(added_domains) < hybrid_domains
        and len(structural_added_subdomains) < hybrid_domains
    )
    if (
        identity_consistent
        and core_retained >= renaming_core_min
        and resp_ok_rename
        and added_structure_insufficient
        and (naming_marker_present or canonical_token_overlap < 0.50)
    ):
        return RelationClassification(
            relation="renaming",
            confidence=_bounded(
                (0.5 + 0.5 * core_retained) * (0.85 if resp_soft else 1.0)
            ),
            reason=(
                "renaming: occupation identity consistent, core retained, "
                "title/marketing wording changed, added structure insufficient"
            ),
            components=components,
        )

    # ── hybridization ──
    # skill-side: two or more mature domain cores with substantive structure.
    # responsibility-side: the JD text itself must evidence >=2 of those
    # domains; taxonomy-only cross-domain is insufficient.
    added_by_domain = _added_skills_per_domain(skill_dim, added_domains)
    added_by_subdomain_resolved = _structural_added_counts(
        skill_dim, structural_added_subdomains
    )
    hybrid_by_domain = (
        len(added_domains) >= hybrid_domains
        and len(added_by_domain) >= hybrid_domains
        and all(
            count >= hybrid_per_domain for count in added_by_domain.values()
        )
    )
    hybrid_by_subdomain = (
        len(structural_added_subdomains) >= hybrid_domains
        and len(added_by_subdomain_resolved) >= hybrid_domains
        and (
            all(
                count >= hybrid_per_domain
                for count in added_by_subdomain_resolved.values()
            )
            or sum(added_by_subdomain_resolved.values()) >= 3
        )
    )
    if hybrid_by_domain or hybrid_by_subdomain:
        evidence_domains = added_domains | added_structural_domains
        resp_evidenced_domains = sorted(
            domain
            for domain in evidence_domains
            if (domain_evidence or {}).get(domain, {}).get("present")
        )
        if len(resp_evidenced_domains) >= resp_domains_min:
            return RelationClassification(
                relation="hybridization",
                confidence=_bounded(
                    min(
                        1.0,
                        max(len(added_domains), len(structural_added_subdomains)) / 3.0,
                    )
                ),
                reason=(
                    "hybridization: >=2 mature domain cores inherited with "
                    "skill structure AND responsibility-side dual-domain evidence"
                ),
                components={
                    **components,
                    "added_skills_per_domain": dict(added_by_domain),
                    "added_skills_per_subdomain": dict(added_by_subdomain_resolved),
                    "responsibility_evidence_domains": resp_evidenced_domains,
                },
            )
        components["hybridization_resp_evidence_missing"] = sorted(
            evidence_domains
            - {
                domain
                for domain in evidence_domains
                if (domain_evidence or {}).get(domain, {}).get("present")
            }
        )

    # ── specialization ──
    # single coherent parent; parent core mostly retained; the added subdomain
    # carries enough structural weight (>=2 added structural skills).
    single_coherent_subdomain = (
        len(structural_added_subdomains) == 1
        and len(added_domains) <= 1
    )
    subdomain_added_count = max(added_by_subdomain.values(), default=0)
    if (
        single_coherent_subdomain
        and relevant_retained >= parent_core_min
        and subdomain_added_count >= subdomain_skills_min
        and structural_weight >= structural_weight_min
    ):
        return RelationClassification(
            relation="specialization",
            confidence=_bounded(
                0.5 + 0.3 * relevant_retained + 0.2 * min(1.0, structural_weight * 2.0)
            ),
            reason=(
                "specialization: single parent, parent core retained, coherent "
                "added subdomain with sufficient structural weight"
            ),
            components={
                **components,
                "added_skills_per_domain": dict(added_by_domain),
                "added_skills_per_subdomain": dict(added_by_subdomain_resolved),
            },
        )

    # ── unexplained_structural_novelty ──
    # not a trash can: parent cannot explain, skill AND responsibility both
    # show reliable novelty, and evidence is sufficient.  In degraded
    # responsibility mode the lexical alignment is not reliable enough to
    # certify unexplained structural novelty, so it never fires there.
    skill_novelty_ok = len(added_structural_skills) >= structural_skill_min
    resp_novelty_ok = (not resp_soft) and resp_changed >= structural_resp_min
    evidence_sufficient = (
        skill_dim.available
        and resp_dim.available
        and taxonomy_available
        and bool(added_structural_skills)
    )
    if (
        skill_novelty_ok
        and resp_novelty_ok
        and evidence_sufficient
    ):
        return RelationClassification(
            relation="unexplained_structural_novelty",
            confidence=_bounded(
                (
                    0.5
                    + 0.25 * min(1.0, len(added_structural_skills) / 6.0)
                    + 0.25 * min(1.0, resp_changed / 4.0)
                )
                * (0.8 if resp_soft else 1.0)
            ),
            reason=(
                "unexplained_structural_novelty: reliable skill AND responsibility "
                "novelty not explained by parent/specialization/hybrid/tool-shift"
            ),
            components=components,
        )

    # ── single-dimension downgrades ──
    if len(added_structural_skills) >= 1 and not resp_novelty_ok:
        if not resp_soft and core_retained >= renaming_core_min:
            return RelationClassification(
                relation="renaming",
                confidence=_bounded(0.45 * core_retained),
                reason=(
                    "single-dimension skill change with parent core retention; "
                    "responsibility novelty insufficient for unexplained_structural_novelty"
                ),
                components=components,
            )
        return RelationClassification(
            relation="insufficient_evidence",
            confidence=_bounded(0.25 if resp_soft else 0.2),
            reason=(
                "skill structural novelty without reliable responsibility "
                "corroboration"
                + (" (degraded lexical responsibility mode)" if resp_soft else "")
            ),
            components=components,
        )
    if resp_novelty_ok and not skill_novelty_ok:
        return RelationClassification(
            relation="insufficient_evidence",
            confidence=0.2,
            reason=(
                "single-dimension responsibility change without skill structural novelty"
            ),
            components=components,
        )
    # no structural addition: the relation depends on identity/core retention
    if not added_structural_skills:
        if core_retained >= renaming_core_min and resp_ok_rename:
            return RelationClassification(
                relation="renaming",
                confidence=_bounded(0.4 + 0.4 * core_retained),
                reason=(
                    "occupation structure retained with no structural addition; "
                    "title wording drifted from the canonical occupation"
                ),
                components=components,
            )
        if core_retained >= same_core_min:
            return RelationClassification(
                relation="same_or_not_novel",
                confidence=_bounded(0.4 + 0.4 * core_retained),
                reason="no material structural change; core retention supports stability",
                components=components,
            )
        return RelationClassification(
            relation="insufficient_evidence",
            confidence=0.2,
            reason=(
                "no structural addition but core retention/identity insufficient "
                "to certify stable or renaming relation"
            ),
            components=components,
        )
    return RelationClassification(
        relation="same_or_not_novel",
        confidence=_bounded(0.3 + 0.3 * core_retained),
        reason="no material structural change against baseline (fallback)",
        components=components,
    )


_NAMING_MARKER_TOKENS = frozenset(
    {
        "ai",
        "llm",
        "agent",
        "智能",
        "大模",
        "模型",
        "数字化",
        "云原",
        "原生",
        "native",
        "vibe",
        "增强",
        "专家",
    }
)


def _title_has_naming_marker(name_dim: DimensionEvidence) -> bool:
    """Marketing/tech-prefix wording signal (generic token set, not case hacks).

    A marker counts only when it is added to the candidate title and is not
    already part of the canonical occupation name (e.g. ``大模型`` is canonical
    for LLM algorithm engineer and therefore not a rename marker there).
    """
    current = str(name_dim.components.get("current_title") or "")
    canonical = str(name_dim.components.get("canonical_title") or "")
    if not current:
        return False
    current_markers = set(_text_tokens(current)) & _NAMING_MARKER_TOKENS
    canonical_markers = set(_text_tokens(canonical)) & _NAMING_MARKER_TOKENS
    return bool(current_markers - canonical_markers)


def _tool_change_ratio(
    skill_dim: DimensionEvidence,
    changed_skill_ids: Sequence[str],
) -> float:
    if not changed_skill_ids:
        return 0.0
    tool_categories = {
        "tool",
        "framework",
        "platform_service",
        "library_sdk",
        "middleware_runtime",
        "protocol_standard",
    }
    changed = set(changed_skill_ids)
    infos = list(skill_dim.components.get("current_skill_infos") or ())
    toolish = sum(
        1
        for info in infos
        if info.get("skill_id") in changed
        and info.get("category_code") in tool_categories
    )
    return _bounded(toolish / len(changed))


def _added_skills_per_domain(
    skill_dim: DimensionEvidence,
    added_domains: frozenset[str],
) -> dict[str, int]:
    current_infos = list(skill_dim.components.get("current_skill_infos") or ())
    before_ids = set(skill_dim.components.get("before_skill_ids") or ())
    counts: dict[str, int] = {}
    for info in current_infos:
        if info.get("skill_id") in before_ids:
            continue
        overlap = added_domains & set(info.get("domains") or ())
        if overlap:
            for domain in overlap:
                counts[domain] = counts.get(domain, 0) + 1
    return counts


def _structural_added_counts(
    skill_dim: DimensionEvidence,
    structural_subdomains: frozenset[str],
) -> dict[str, int]:
    current_infos = list(skill_dim.components.get("current_skill_infos") or ())
    before_ids = set(skill_dim.components.get("before_skill_ids") or ())
    non_structural_categories = {
        "programming_language",
        "tool",
        "framework",
        "platform_service",
        "library_sdk",
        "middleware_runtime",
        "protocol_standard",
    }
    counts: dict[str, int] = {}
    for info in current_infos:
        if info.get("skill_id") in before_ids:
            continue
        if info.get("category_code") in non_structural_categories:
            continue
        subcategory = info.get("subcategory_code")
        if subcategory in structural_subdomains:
            counts[subcategory] = counts.get(subcategory, 0) + 1
    return counts


# ── Stage 2 classification ──


def classify_emergence_v2(
    *,
    relation: Stage1Relation,
    relation_confidence: float,
    skill_dim: DimensionEvidence,
    market_dim: DimensionEvidence,
    config: Mapping[str, object] | None = None,
) -> EmergenceClassification:
    """Stage 2 emergence state; requires structural + temporal + diffusion."""
    effective = _effective_config(config)
    em_cfg = dict(effective["emergence"])
    mkt_cfg = dict(effective["market"])
    min_enterprises = int(mkt_cfg["min_enterprise_diffusion"])
    structural_relations = set(em_cfg["structural_relations"])

    if relation == "insufficient_evidence":
        return EmergenceClassification(
            state="insufficient_evidence",
            confidence=0.0,
            reason="relation evidence insufficient",
        )
    structural_ok = relation in structural_relations
    if not structural_ok:
        return EmergenceClassification(
            state="not_emerging",
            confidence=_bounded(0.6 * relation_confidence),
            reason=f"relation '{relation}' is not structural novelty",
        )
    if not market_dim.available:
        return EmergenceClassification(
            state="insufficient_evidence",
            confidence=0.0,
            reason="structural novelty but no market/temporal evidence",
        )
    components = dict(market_dim.components)
    growth_available = bool(components.get("growth_available"))
    persistence = float(components.get("persistence", 0.0))
    multi_enterprise = bool(components.get("multi_enterprise"))
    enterprise_growth = int(components.get("enterprise_growth", 0))
    source_concentrated = bool(components.get("source_concentrated"))
    temporal_ok = growth_available and persistence >= 1.0
    diffusion_ok = multi_enterprise or enterprise_growth > 0

    if not temporal_ok:
        if skill_dim.available and (skill_dim.score or 0.0) >= 0.45:
            return EmergenceClassification(
                state="weak_emerging_signal",
                confidence=_bounded(0.30 * relation_confidence),
                reason="structural novelty but no >=2 valid time windows (growth unavailable)",
                components=components,
            )
        return EmergenceClassification(
            state="insufficient_evidence",
            confidence=0.0,
            reason="structural novelty but temporal evidence unavailable",
            components=components,
        )
    if not diffusion_ok:
        return EmergenceClassification(
            state="weak_emerging_signal",
            confidence=_bounded(0.35 * relation_confidence),
            reason=(
                f"structural novelty + temporal evidence but no multi-enterprise "
                f"diffusion (enterprises<{min_enterprises})"
            ),
            components=components,
        )
    confidence = _bounded(
        relation_confidence * (market_dim.confidence or 0.0)
    )
    if source_concentrated:
        confidence = _bounded(
            confidence * float(mkt_cfg["source_concentrated_confidence_multiplier"])
        )
        return EmergenceClassification(
            state="emerging",
            confidence=confidence,
            reason="emerging with source-concentrated confidence downgrade",
            components=components,
        )
    return EmergenceClassification(
        state="emerging",
        confidence=confidence,
        reason="structural novelty + temporal persistence/growth + market diffusion",
        components=components,
    )


# ── Presentation compatibility with the old six-class API ──


def map_to_v1_class(relation: Stage1Relation, state: Stage2State) -> str:
    """Map v2 inference to the old six-class presentation vocabulary."""
    mapping: dict[tuple[Stage1Relation, Stage2State], str] = {
        ("same_or_not_novel", "not_emerging"): "unclassified",
        ("same_or_not_novel", "weak_emerging_signal"): "unclassified",
        ("same_or_not_novel", "emerging"): "unclassified",
        ("same_or_not_novel", "insufficient_evidence"): "unclassified",
        ("renaming", "not_emerging"): "renaming",
        ("renaming", "weak_emerging_signal"): "renaming",
        ("renaming", "emerging"): "renaming",
        ("renaming", "insufficient_evidence"): "renaming",
        ("specialization", "not_emerging"): "specialization",
        ("specialization", "weak_emerging_signal"): "specialization",
        ("specialization", "emerging"): "specialization",
        ("specialization", "insufficient_evidence"): "specialization",
        ("hybridization", "not_emerging"): "hybridization",
        ("hybridization", "weak_emerging_signal"): "hybridization",
        ("hybridization", "emerging"): "hybridization",
        ("hybridization", "insufficient_evidence"): "hybridization",
        ("tool_shift", "not_emerging"): "tool_shift",
        ("tool_shift", "weak_emerging_signal"): "tool_shift",
        ("tool_shift", "emerging"): "tool_shift",
        ("tool_shift", "insufficient_evidence"): "tool_shift",
        ("unexplained_structural_novelty", "emerging"): "genuine_emergence",
        ("unexplained_structural_novelty", "weak_emerging_signal"): "unclassified",
        ("unexplained_structural_novelty", "not_emerging"): "unclassified",
        ("unexplained_structural_novelty", "insufficient_evidence"): "unclassified",
        ("insufficient_evidence", "insufficient_evidence"): "unclassified",
        ("insufficient_evidence", "not_emerging"): "unclassified",
        ("insufficient_evidence", "weak_emerging_signal"): "unclassified",
        ("insufficient_evidence", "emerging"): "unclassified",
    }
    return mapping.get((relation, state), "unclassified")


__all__ = [
    "DEFAULT_EMERGENCE_V2_CONFIG",
    "STAGE1_RELATIONS",
    "STAGE2_STATES",
    "CandidateMarketStats",
    "DimensionEvidence",
    "EmbeddingSemanticEncoder",
    "EmergenceClassification",
    "LexicalFallbackEncoder",
    "PositionIndex",
    "PositionInfo",
    "RelationClassification",
    "SemanticTextEncoder",
    "SkillIndex",
    "SkillInfo",
    "build_position_index",
    "build_skill_index",
    "classify_emergence_v2",
    "classify_relation_v2",
    "map_to_v1_class",
    "policy_config_version",
    "score_industry_scenario_v2",
    "score_market_behavior_v2",
    "score_name_novelty_v2",
    "score_responsibility_structure_v2",
    "score_skill_combination_novelty_v2",
]
