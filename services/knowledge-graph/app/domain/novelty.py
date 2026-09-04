"""TEMP-08: Five-dimension novelty scoring + TEMP-08-CLASS event classification.

LEGACY (archived, not the active chain): the formal EMERGE policy is now
the ``app.emergence`` package (v3.2 final solution), exposed through
``app.emergence.EmergenceV32Policy``.

Each dimension is independently scored. Missing data → available=False, never synthesizes
a total score. classify_novelty_event() maps the 5-dim profile to one of 6 event types.
"""

from __future__ import annotations

import math
import re
import statistics
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.domain.evolution import _bounded

NoveltyDimension = Literal[
    "name_novelty",
    "skill_combination_novelty",
    "responsibility_structure_novelty",
    "industry_scenario_novelty",
    "market_behavior_novelty",
    "semantic_novelty",
]

NoveltyClassification = Literal[
    "renaming",
    "specialization",
    "hybridization",
    "tool_shift",
    "genuine_emergence",
    "unclassified",
]

DEFAULT_NOVELTY_CONFIG: dict[str, object] = {
    "policy_version": "five-dim-novelty-v1",
    "name_novelty": {
        "high_edit_distance_threshold": 0.60,
        "medium_edit_distance_threshold": 0.30,
    },
    "skill_combination": {
        "high_novelty_jaccard_threshold": 0.70,
        "novel_combination_jaccard_threshold": 0.50,
    },
    "responsibility": {
        "high_novelty_token_threshold": 0.60,
    },
    "industry": {
        "novel_industry_code_threshold": 0.30,
    },
    "market_behavior": {
        "min_enterprise_count": 3,
        "min_growth_factor": 1.5,
        # 来源集中惩罚：来源数 < min_source_diversity 或单源占比 >
        # max_source_concentration 时，市场行为证据不可靠，不给正结论。
        "min_source_diversity": 2,
        "max_source_concentration": 0.8,
    },
    "classification": {
        "renaming_min_name_confidence": 0.70,
        "renaming_max_skill_novelty": 0.30,
        "renaming_max_responsibility_novelty": 0.30,
        "specialization_min_skill_reduction": 0.25,
        "hybridization_min_new_domains": 2,
        "hybridization_min_skills_per_domain": 2,
        "tool_shift_max_category_change": 0.15,
        "genuine_emergence_min_novel_dimensions": 3,
        "genuine_emergence_min_dimension_score": 0.50,
        "genuine_emergence_min_market_score": 0.20,
        "template_copy_max_resp_score": 0.20,
    },
}


@dataclass(frozen=True)
class NoveltyDimensionResult:
    dimension: NoveltyDimension
    available: bool
    score: float | None
    reason: str
    evidence_components: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class FiveDimNoveltyResult:
    position_id: str
    release_id: str
    graph_version_id: int
    dimensions: tuple[NoveltyDimensionResult, ...]
    policy_version: str
    computed_at: str

    def available_dimensions(self) -> tuple[NoveltyDimensionResult, ...]:
        return tuple(d for d in self.dimensions if d.available)

    def unavailable_dimensions(self) -> tuple[NoveltyDimensionResult, ...]:
        return tuple(d for d in self.dimensions if not d.available)


@dataclass(frozen=True)
class NoveltyEventClassification:
    position_id: str
    from_version_id: int
    to_version_id: int
    classification: NoveltyClassification
    novelty_profile: FiveDimNoveltyResult
    confidence: float
    evidence: dict[str, object]
    policy_version: str
    classified_at: str


# ── Dimension scorers ──

def _edit_distance(a: str, b: str) -> float:
    """Normalised Levenshtein ratio in [0, 1]."""
    if not a and not b:
        return 0.0
    if not a or not b:
        return 1.0
    len_a, len_b = len(a), len(b)

    prev = list(range(len_b + 1))
    curr = [0] * (len_b + 1)
    for i in range(1, len_a + 1):
        curr[0] = i
        for j in range(1, len_b + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev, curr = curr, prev
    return prev[len_b] / max(len_a, len_b)


_CJK_RE = r"[㐀-䶿一-鿿豈-﫿]"
_RUN_RE = re.compile(rf"[0-9a-z]+|{_CJK_RE}+")


def _cjk_bigrams(run: str) -> set[str]:
    if len(run) <= 1:
        return {run}
    return {run[i : i + 2] for i in range(len(run) - 1)}


def _token_set(s: str) -> frozenset[str]:
    """Deterministic token set for mixed CJK/ASCII text.

    NFKC-normalises and lowercases, then drops punctuation. ASCII runs are kept
    as whole words; CJK runs are expanded to character bigrams so that a
    space-less Chinese responsibility is not treated as a single token.
    """
    text = unicodedata.normalize("NFKC", s).lower()
    tokens: set[str] = set()
    for run in _RUN_RE.findall(text):
        if run[0].isascii():
            tokens.add(run)
        else:
            tokens.update(_cjk_bigrams(run))
    return frozenset(tokens)


def _jaccard_distance(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return 1.0 - intersection / union if union else 1.0


def score_name_novelty(
    current_name: str,
    *,
    before_name: str | None = None,
    historic_names: tuple[str, ...] = (),
    catalog_taxonomy_version: str = "",
    config: Mapping[str, object] | None = None,
) -> NoveltyDimensionResult:
    effective = config or DEFAULT_NOVELTY_CONFIG
    name_cfg = dict(effective["name_novelty"])
    if not before_name and not historic_names:
        return NoveltyDimensionResult(
            dimension="name_novelty",
            available=False,
            score=None,
            reason="NO_DATA: no before_name or historic_names available",
        )
    distances = []
    if before_name:
        distances.append(_edit_distance(current_name, before_name))
    for hname in historic_names:
        distances.append(_edit_distance(current_name, hname))
    best_dist = min(distances) if distances else 1.0
    high_threshold = float(name_cfg["high_edit_distance_threshold"])
    med_threshold = float(name_cfg["medium_edit_distance_threshold"])
    if best_dist >= high_threshold:
        score = _bounded(0.7 + 0.3 * (best_dist - high_threshold) / max(1 - high_threshold, 0.01))
    elif best_dist >= med_threshold:
        score = _bounded(0.4 + 0.3 * (best_dist - med_threshold) / max(high_threshold - med_threshold, 0.01))
    else:
        score = _bounded(0.4 * best_dist / max(med_threshold, 0.01))
    return NoveltyDimensionResult(
        dimension="name_novelty",
        available=True,
        score=score,
        reason=f"edit_distance={best_dist:.3f}",
        evidence_components={
            "edit_distance": round(best_dist, 4),
            "catalog_taxonomy_version": catalog_taxonomy_version,
            "compared_against": list(historic_names) + ([before_name] if before_name else []),
        },
    )


def score_skill_combination_novelty(
    current_skills: tuple[str, ...],
    *,
    before_skills: tuple[str, ...] | None = None,
    peer_skill_sets: tuple[tuple[str, ...], ...] = (),
    config: Mapping[str, object] | None = None,
) -> NoveltyDimensionResult:
    effective = config or DEFAULT_NOVELTY_CONFIG
    skill_cfg = dict(effective["skill_combination"])
    if before_skills is None:
        return NoveltyDimensionResult(
            dimension="skill_combination_novelty",
            available=False,
            score=None,
            reason="NO_DATA: no before_skills available",
        )
    current_set = frozenset(current_skills)
    before_set = frozenset(before_skills)
    vs_before = _jaccard_distance(current_set, before_set)
    peer_distances = [
        _jaccard_distance(current_set, frozenset(ps))
        for ps in peer_skill_sets
    ]
    min_peer_dist = min(peer_distances) if peer_distances else vs_before
    combined_dist = 0.6 * vs_before + 0.4 * min_peer_dist
    high_threshold = float(skill_cfg["high_novelty_jaccard_threshold"])
    mid_threshold = float(skill_cfg["novel_combination_jaccard_threshold"])
    if combined_dist >= high_threshold:
        score = _bounded(0.7 + 0.3 * (combined_dist - high_threshold) / max(1 - high_threshold, 0.01))
    elif combined_dist >= mid_threshold:
        score = _bounded(0.4 + 0.3 * (combined_dist - mid_threshold) / max(high_threshold - mid_threshold, 0.01))
    else:
        score = _bounded(0.4 * combined_dist / max(mid_threshold, 0.01))
    return NoveltyDimensionResult(
        dimension="skill_combination_novelty",
        available=True,
        score=score,
        reason=f"jaccard_vs_before={vs_before:.3f}, min_peer_jaccard={min_peer_dist:.3f}",
        evidence_components={
            "jaccard_vs_before": round(vs_before, 4),
            "min_peer_jaccard": round(min_peer_dist, 4),
            "current_skill_count": len(current_skills),
            "before_skill_count": len(before_skills),
        },
    )


def score_responsibility_structure_novelty(
    current_responsibilities: tuple[str, ...],
    *,
    before_responsibilities: tuple[str, ...] | None = None,
    peer_responsibility_sets: tuple[tuple[str, ...], ...] = (),
    config: Mapping[str, object] | None = None,
) -> NoveltyDimensionResult:
    effective = config or DEFAULT_NOVELTY_CONFIG
    resp_cfg = dict(effective["responsibility"])
    if before_responsibilities is None:
        return NoveltyDimensionResult(
            dimension="responsibility_structure_novelty",
            available=False,
            score=None,
            reason="NO_DATA: no before_responsibilities available",
        )
    curr_tokens = frozenset.union(*(_token_set(r) for r in current_responsibilities)) if current_responsibilities else frozenset()
    before_tokens = frozenset.union(*(_token_set(r) for r in before_responsibilities)) if before_responsibilities else frozenset()
    vs_before = _jaccard_distance(curr_tokens, before_tokens)
    peer_dists = []
    for prs in peer_responsibility_sets:
        peer_tokens = frozenset.union(*(_token_set(r) for r in prs)) if prs else frozenset()
        peer_dists.append(_jaccard_distance(curr_tokens, peer_tokens))
    min_peer_dist = min(peer_dists) if peer_dists else vs_before
    combined_dist = 0.6 * vs_before + 0.4 * min_peer_dist
    high_threshold = float(resp_cfg["high_novelty_token_threshold"])
    score = _bounded(combined_dist / high_threshold) if high_threshold > 0 else _bounded(combined_dist)
    return NoveltyDimensionResult(
        dimension="responsibility_structure_novelty",
        available=True,
        score=score,
        reason=f"token_jaccard_vs_before={vs_before:.3f}, min_peer={min_peer_dist:.3f}",
        evidence_components={
            "token_jaccard_vs_before": round(vs_before, 4),
            "min_peer_token_jaccard": round(min_peer_dist, 4),
        },
    )


def score_industry_scenario_novelty(
    industry_codes: tuple[str, ...],
    *,
    historic_industry_codes: tuple[str, ...] = (),
    config: Mapping[str, object] | None = None,
) -> NoveltyDimensionResult:
    effective = config or DEFAULT_NOVELTY_CONFIG
    ind_cfg = dict(effective["industry"])
    if not industry_codes:
        return NoveltyDimensionResult(
            dimension="industry_scenario_novelty",
            available=False,
            score=None,
            reason="NO_DATA: no industry_codes available",
        )
    current_set = frozenset(industry_codes)
    historic_set = frozenset(historic_industry_codes)
    new_codes = current_set - historic_set
    if not historic_set:
        return NoveltyDimensionResult(
            dimension="industry_scenario_novelty",
            available=False,
            score=None,
            reason="NO_DATA: NO_HISTORICAL_INDUSTRY_BASELINE",
            evidence_components={
                "total_current_codes": len(current_set),
                "historic_code_count": 0,
            },
        )
    novelty_ratio = len(new_codes) / max(len(current_set), 1)
    threshold = float(ind_cfg["novel_industry_code_threshold"])
    score = _bounded(novelty_ratio / threshold) if threshold > 0 else _bounded(novelty_ratio)
    return NoveltyDimensionResult(
        dimension="industry_scenario_novelty",
        available=True,
        score=score,
        reason=f"new_industry_codes={len(new_codes)}/{len(current_set)}",
        evidence_components={
            "new_industry_codes": sorted(new_codes),
            "total_current_codes": len(current_set),
            "historic_code_count": len(historic_set),
        },
    )


def score_market_behavior_novelty(
    jd_count: int,
    enterprise_count: int,
    geographic_spread: int = 0,
    *,
    growth_trajectory: tuple[int, ...] = (),
    peer_trajectories: tuple[tuple[int, ...], ...] = (),
    source_diversity: int | None = None,
    max_source_share: float | None = None,
    config: Mapping[str, object] | None = None,
) -> NoveltyDimensionResult:
    effective = config or DEFAULT_NOVELTY_CONFIG
    mkt_cfg = dict(effective["market_behavior"])
    min_enterprises = int(mkt_cfg["min_enterprise_count"])
    min_growth = float(mkt_cfg["min_growth_factor"])
    min_sources = int(mkt_cfg.get("min_source_diversity", 2))
    max_conc = float(mkt_cfg.get("max_source_concentration", 0.8))
    if jd_count == 0 and enterprise_count == 0 and not growth_trajectory:
        return NoveltyDimensionResult(
            dimension="market_behavior_novelty",
            available=False,
            score=None,
            reason="NO_DATA: no market behavior data",
        )
    # 来源集中惩罚：单源或单源占比过高时，市场行为证据不可靠，降权但不硬否决
    # （source_concentrated 不代表市场无 traction，只是证据不可靠）。
    source_concentrated = (
        (source_diversity is not None and source_diversity < min_sources)
        or (max_source_share is not None and max_source_share > max_conc)
    )
    if enterprise_count < min_enterprises:
        return NoveltyDimensionResult(
            dimension="market_behavior_novelty",
            available=True,
            score=0.1,
            reason=f"single/small enterprise cluster ({enterprise_count} < {min_enterprises})",
            evidence_components={"enterprise_count": enterprise_count, "jd_count": jd_count},
        )
    # Growth: log1p delta supports 0-baseline first appearance (0 -> N) without
    # division by zero, so a sudden first appearance is scored as real growth.
    growth_norm = math.log1p(max(min_growth, 0.0))
    if len(growth_trajectory) >= 2:
        prev = int(growth_trajectory[-2])
        curr = int(growth_trajectory[-1])
        growth_delta = math.log1p(max(curr, 0)) - math.log1p(max(prev, 0))
        growth_factor = math.exp(growth_delta)
    else:
        growth_delta = 0.0
        growth_factor = 1.0
    if growth_delta >= 0:
        growth_score = _bounded(growth_delta / max(growth_norm, 0.01))
    else:
        growth_score = 0.1
    # Peer-relative growth: nudge the growth score by how the target's growth
    # compares to the median peer growth. Deterministic; a no-op without peers.
    peer_relative_delta: float | None = None
    peer_count = 0
    if peer_trajectories and len(growth_trajectory) >= 2:
        peer_deltas = [
            math.log1p(max(int(t[-1]), 0)) - math.log1p(max(int(t[-2]), 0))
            for t in peer_trajectories
            if len(t) >= 2
        ]
        peer_count = len(peer_deltas)
        if peer_deltas:
            peer_relative_delta = growth_delta - statistics.median(peer_deltas)
            growth_score = _bounded(growth_score + 0.5 * peer_relative_delta / max(growth_norm, 0.01))
    # enterprise diversity
    enterprise_score = _bounded(enterprise_count / 10.0)
    # geographic spread
    geo_score = _bounded(geographic_spread / 5.0)
    score = _bounded(0.4 * growth_score + 0.35 * enterprise_score + 0.25 * geo_score)
    reason = f"growth_factor={growth_factor:.2f}, enterprises={enterprise_count}, geo_spread={geographic_spread}"
    if source_concentrated:
        # 来源集中降权：市场分减半并封顶 0.4，使其不可能作为"novel"维度（<0.5），
        # 也不触发 market veto（>=0.2），只是不把不可靠的市场证据当正信号。
        score = min(_bounded(score * 0.5), 0.4)
        reason = "source_concentrated(downgraded); " + reason
    return NoveltyDimensionResult(
        dimension="market_behavior_novelty",
        available=True,
        score=score,
        reason=reason,
        evidence_components={
            "jd_count": jd_count,
            "enterprise_count": enterprise_count,
            "geographic_spread": geographic_spread,
            "growth_factor": round(growth_factor, 4),
            "growth_delta": round(growth_delta, 4),
            "peer_count": peer_count,
            "peer_relative_delta": round(peer_relative_delta, 4) if peer_relative_delta is not None else None,
            "source_diversity": source_diversity,
            "max_source_share": round(max_source_share, 4) if max_source_share is not None else None,
        },
    )


# ── Five-dim entry point ──

def score_semantic_novelty(
    provider: object | None,
    *,
    baselines: tuple[tuple[str, str, str], ...],
    candidate_name: str,
    candidate_skills: str,
    candidate_responsibilities: str,
) -> NoveltyDimensionResult:
    """语义新颖性维度：用 LLM 语义判断替代字符级编辑距离/Jaccard。

    provider 需提供 ``judge(**kwargs) -> SemanticSimilarityJudgment``，其中
    judgment 有 ``similarity``(0..1)、``same_domain``(bool)、``reason`` 三个属性。

    多基准（最近邻）语义判定：候选与每个成熟基准比较，若与任一基准同域
    （same_domain=true）则视为对该基准的细分/换皮（低新颖性）；仅当与全部基准
    都不同域时才判为 genuine emergence。无 provider（LLM 未启用）时返回 unavailable。
    """
    if provider is None:
        return NoveltyDimensionResult(
            dimension="semantic_novelty",
            available=False,
            score=None,
            reason="NO_DATA: no semantic provider configured (LLM disabled)",
        )
    best_similarity = 0.0
    any_same_domain = False
    same_domain_baselines: list[str] = []
    for bname, bskills, bresp in baselines:
        judgment = provider.judge(
            baseline_name=bname,
            baseline_skills=bskills,
            baseline_responsibilities=bresp,
            candidate_name=candidate_name,
            candidate_skills=candidate_skills,
            candidate_responsibilities=candidate_responsibilities,
        )
        similarity = max(0.0, min(1.0, float(judgment.similarity)))
        best_similarity = max(best_similarity, similarity)
        if judgment.same_domain:
            any_same_domain = True
            same_domain_baselines.append(bname)
    if any_same_domain:
        # 与某基准同域 → 是细分/换皮，语义新颖性低（封顶 0.2）
        score = _bounded(0.2 * (1.0 - best_similarity))
    else:
        # 与全部基准都不同域 → 语义新颖性高（0.7 起）
        score = _bounded(0.7 + 0.3 * (1.0 - best_similarity))
    reason = (
        f"any_same_domain={any_same_domain}, max_similarity={best_similarity:.2f}"
    )
    if same_domain_baselines:
        reason += "; same_domain_baselines=" + ",".join(same_domain_baselines)
    return NoveltyDimensionResult(
        dimension="semantic_novelty",
        available=True,
        score=score,
        reason=reason,
        evidence_components={
            "max_similarity": round(best_similarity, 4),
            "any_same_domain": any_same_domain,
            "baseline_count": len(baselines),
        },
    )


def compute_five_dim_novelty(
    position_id: str,
    release_id: str,
    graph_version_id: int,
    *,
    current_name: str,
    current_skills: tuple[str, ...] = (),
    current_responsibilities: tuple[str, ...] = (),
    before_name: str | None = None,
    before_skills: tuple[str, ...] | None = None,
    before_responsibilities: tuple[str, ...] | None = None,
    historic_names: tuple[str, ...] = (),
    peer_skill_sets: tuple[tuple[str, ...], ...] = (),
    peer_responsibility_sets: tuple[tuple[str, ...], ...] = (),
    industry_codes: tuple[str, ...] = (),
    historic_industry_codes: tuple[str, ...] = (),
    jd_count: int = 0,
    enterprise_count: int = 0,
    geographic_spread: int = 0,
    growth_trajectory: tuple[int, ...] = (),
    peer_trajectories: tuple[tuple[int, ...], ...] = (),
    source_diversity: int | None = None,
    max_source_share: float | None = None,
    semantic_provider: object | None = None,
    semantic_baselines: tuple[tuple[str, str, str], ...] | None = None,
    catalog_taxonomy_version: str = "",
    computed_at: str | None = None,
    config: Mapping[str, object] | None = None,
) -> FiveDimNoveltyResult:
    effective = dict(DEFAULT_NOVELTY_CONFIG, **(config or {}))
    dims = (
        score_name_novelty(
            current_name,
            before_name=before_name,
            historic_names=historic_names,
            catalog_taxonomy_version=catalog_taxonomy_version,
            config=effective,
        ),
        score_skill_combination_novelty(
            current_skills,
            before_skills=before_skills,
            peer_skill_sets=peer_skill_sets,
            config=effective,
        ),
        score_responsibility_structure_novelty(
            current_responsibilities,
            before_responsibilities=before_responsibilities,
            peer_responsibility_sets=peer_responsibility_sets,
            config=effective,
        ),
        score_industry_scenario_novelty(
            industry_codes,
            historic_industry_codes=historic_industry_codes,
            config=effective,
        ),
        score_market_behavior_novelty(
            jd_count,
            enterprise_count,
            geographic_spread,
            growth_trajectory=growth_trajectory,
            peer_trajectories=peer_trajectories,
            source_diversity=source_diversity,
            max_source_share=max_source_share,
            config=effective,
        ),
        score_semantic_novelty(
            semantic_provider,
            baselines=semantic_baselines or (
                (
                    before_name or "",
                    ", ".join(before_skills or ()),
                    ", ".join(before_responsibilities or ()),
                ),
            ),
            candidate_name=current_name,
            candidate_skills=", ".join(current_skills),
            candidate_responsibilities=", ".join(current_responsibilities),
        ),
    )
    return FiveDimNoveltyResult(
        position_id=position_id,
        release_id=release_id,
        graph_version_id=graph_version_id,
        dimensions=dims,
        policy_version=str(effective.get("policy_version", "five-dim-novelty-v1")),
        computed_at=computed_at or "",
    )


# ── Classification ──

def classify_novelty_event(
    novelty: FiveDimNoveltyResult,
    *,
    position_id: str = "",
    from_version_id: int = 0,
    to_version_id: int = 0,
    before_skills: tuple[str, ...] = (),
    current_skills: tuple[str, ...] = (),
    classified_at: str | None = None,
    config: Mapping[str, object] | None = None,
) -> NoveltyEventClassification:
    effective = config or DEFAULT_NOVELTY_CONFIG
    cls_cfg = dict(effective["classification"])
    dims_by_name = {d.dimension: d for d in novelty.dimensions}

    name = dims_by_name.get("name_novelty")
    skill = dims_by_name.get("skill_combination_novelty")
    resp = dims_by_name.get("responsibility_structure_novelty")
    market = dims_by_name.get("market_behavior_novelty")
    semantic = dims_by_name.get("semantic_novelty")

    name_score = name.score if name and name.available else None
    skill_score = skill.score if skill and skill.available else None
    resp_score = resp.score if resp and resp.available else None

    pid = position_id or novelty.position_id

    # renaming: high name novelty, low skill+responsibility novelty
    renaming_name = float(cls_cfg["renaming_min_name_confidence"])
    renaming_skill_max = float(cls_cfg["renaming_max_skill_novelty"])
    renaming_resp_max = float(cls_cfg["renaming_max_responsibility_novelty"])
    if (
        name_score is not None and name_score >= renaming_name
        and (skill_score is None or skill_score < renaming_skill_max)
        and (resp_score is None or resp_score < renaming_resp_max)
    ):
        return NoveltyEventClassification(
            position_id=pid,
            from_version_id=from_version_id,
            to_version_id=to_version_id,
            classification="renaming",
            novelty_profile=novelty,
            confidence=_bounded(name_score),
            evidence={"trigger": "high_name_novelty_low_structure_novelty"},
            policy_version=novelty.policy_version,
            classified_at=classified_at or "",
        )

    # specialization: skill set narrowed
    if before_skills and current_skills:
        before_set = frozenset(before_skills)
        current_set = frozenset(current_skills)
        if current_set.issubset(before_set) and len(current_set) < len(before_set):
            reduction = 1.0 - len(current_set) / max(len(before_set), 1)
            spec_min = float(cls_cfg["specialization_min_skill_reduction"])
            if reduction >= spec_min:
                return NoveltyEventClassification(
                    position_id=pid,
                    from_version_id=from_version_id,
                    to_version_id=to_version_id,
                    classification="specialization",
                    novelty_profile=novelty,
                    confidence=_bounded(reduction),
                    evidence={"trigger": "skill_set_narrowed", "skill_reduction": round(reduction, 4)},
                    policy_version=novelty.policy_version,
                    classified_at=classified_at or "",
                )

    # hybridization: new category domains appearing
    if before_skills and current_skills:
        # group skills by category code prefix
        def _category_groups(skills: tuple[str, ...]) -> set[str]:
            return {s.split("_")[0] if "_" in s else s for s in skills}
        before_cats = _category_groups(before_skills)
        current_cats = _category_groups(current_skills)
        new_cats = current_cats - before_cats
        min_domains = int(cls_cfg["hybridization_min_new_domains"])
        min_per_domain = int(cls_cfg["hybridization_min_skills_per_domain"])
        new_domain_count = 0
        for cat in new_cats:
            cat_skills = [s for s in current_skills if s.startswith(cat)]
            if len(cat_skills) >= min_per_domain:
                new_domain_count += 1
        if new_domain_count >= min_domains:
            return NoveltyEventClassification(
                position_id=pid,
                from_version_id=from_version_id,
                to_version_id=to_version_id,
                classification="hybridization",
                novelty_profile=novelty,
                confidence=_bounded(new_domain_count / 3.0),
                evidence={"trigger": "new_category_domains", "new_domain_count": new_domain_count},
                policy_version=novelty.policy_version,
                classified_at=classified_at or "",
            )

    # tool_shift: skills changed but same category
    if before_skills and current_skills:
        def _categories(skills: tuple[str, ...]) -> set[str]:
            return {s.split("_")[0] if "_" in s else s for s in skills}
        before_cats = _categories(before_skills)
        current_cats = _categories(current_skills)
        cat_overlap = len(before_cats & current_cats)
        total_cats = len(before_cats | current_cats)
        cat_change = 1.0 - cat_overlap / max(total_cats, 1)
        tool_max = float(cls_cfg["tool_shift_max_category_change"])
        if cat_change <= tool_max and before_cats != current_cats:
            return NoveltyEventClassification(
                position_id=pid,
                from_version_id=from_version_id,
                to_version_id=to_version_id,
                classification="tool_shift",
                novelty_profile=novelty,
                confidence=_bounded(1.0 - cat_change),
                evidence={"trigger": "same_category_tool_change", "category_change": round(cat_change, 4)},
                policy_version=novelty.policy_version,
                classified_at=classified_at or "",
            )

    # genuine_emergence: >=N dimensions score high
    min_novel_dims = int(cls_cfg["genuine_emergence_min_novel_dimensions"])
    min_dim_score = float(cls_cfg["genuine_emergence_min_dimension_score"])

    # Market veto: if market behavior is available but very low, the
    # "emergence" lacks real market traction — block genuine_emergence.
    market_min = float(cls_cfg.get("genuine_emergence_min_market_score", 0.20))
    if market and market.available and market.score is not None and market.score < market_min:
        return NoveltyEventClassification(
            position_id=pid,
            from_version_id=from_version_id,
            to_version_id=to_version_id,
            classification="unclassified",
            novelty_profile=novelty,
            confidence=0.0,
            evidence={"trigger": "market_veto", "market_score": market.score},
            policy_version=novelty.policy_version,
            classified_at=classified_at or "",
        )

    # Semantic veto: 语义维度明确判为"同一技术方向"时，不是 genuine_emergence，
    # 而是对基准的细分/换皮（语义新颖性 <0.5）。语义信号优先于字符级三维度。
    if (
        semantic and semantic.available and semantic.score is not None
        and semantic.score < 0.5
    ):
        return NoveltyEventClassification(
            position_id=pid,
            from_version_id=from_version_id,
            to_version_id=to_version_id,
            classification="unclassified",
            novelty_profile=novelty,
            confidence=0.0,
            evidence={"trigger": "semantic_veto", "semantic_score": semantic.score},
            policy_version=novelty.policy_version,
            classified_at=classified_at or "",
        )

    # Template copy detection: responsibilities unchanged but name/skills
    # shifted → title rebranding on the same JD template, not emergence.
    resp_max = float(cls_cfg.get("template_copy_max_resp_score", 0.20))
    if (
        resp and resp.available and resp.score is not None and resp.score < resp_max
        and name_score is not None and name_score >= 0.30
    ):
        return NoveltyEventClassification(
            position_id=pid,
            from_version_id=from_version_id,
            to_version_id=to_version_id,
            classification="renaming",
            novelty_profile=novelty,
            confidence=_bounded(name_score),
            evidence={"trigger": "template_copy", "resp_score": resp.score},
            policy_version=novelty.policy_version,
            classified_at=classified_at or "",
        )

    available_dims = novelty.available_dimensions()
    novel_count = sum(
        1 for d in available_dims
        if d.score is not None and d.score >= min_dim_score
    )
    if novel_count >= min_novel_dims:
        # build confidence from the novel dimension scores
        novel_scores = [d.score for d in available_dims if d.score is not None and d.score >= min_dim_score]
        avg_novel = sum(novel_scores) / len(novel_scores) if novel_scores else 0.0
        return NoveltyEventClassification(
            position_id=pid,
            from_version_id=from_version_id,
            to_version_id=to_version_id,
            classification="genuine_emergence",
            novelty_profile=novelty,
            confidence=_bounded(avg_novel),
            evidence={
                "trigger": "multi_dimension_novelty",
                "novel_dimension_count": novel_count,
                "novel_dimensions": [d.dimension for d in available_dims if d.score is not None and d.score >= min_dim_score],
            },
            policy_version=novelty.policy_version,
            classified_at=classified_at or "",
        )

    # unclassified
    return NoveltyEventClassification(
        position_id=pid,
        from_version_id=from_version_id,
        to_version_id=to_version_id,
        classification="unclassified",
        novelty_profile=novelty,
        confidence=0.0,
        evidence={"trigger": "no_rule_matched"},
        policy_version=novelty.policy_version,
        classified_at=classified_at or "",
    )
