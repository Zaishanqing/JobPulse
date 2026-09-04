"""EXP-EMERGE-01 v3.1 method correction.

Two corrections on top of the stabilized v2.1 policy:

1. Temporal evidence semantics
   - re-observation persistence (same evidence identity / same content hash
     captured on multiple dates) is separated from independent posting
     persistence (different evidence identities continuing across windows),
     enterprise diffusion, source diffusion, structural evolution and market
     growth.
   - A single evidence identity repeated across dates contributes only
     ``re_observation_persistence``; it never contributes to growth or
     diffusion.  Stage-2 ``emerging`` therefore cannot be reached from
     repeated crawls of the same JD plus family-level context.

2. Mature-reference explanation
   - A mature occupation reference bank is built from the frozen position
     taxonomy + the frozen bundle JDs (per family profiles of skills and
     responsibilities).
   - Candidates are first explained against top-k mature references using
     title semantic similarity + normalised skill structure + responsibility
     embedding.  Only when all top-k references fail AND skill novelty and
     semantic responsibility novelty are both reliable may the relation be
     ``unexplained_structural_novelty``.

All experiment logic lives under ``experiments/``; no production service code
is modified.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.emergence.emergence_v2 import (
    EmbeddingSemanticEncoder,
    LexicalFallbackEncoder,
    RelationClassification,
    classify_emergence_v2,
    classify_relation_v2,
    map_to_v1_class,
    responsibility_domain_evidence,
    score_market_behavior_v2,
    score_name_novelty_v2,
    score_responsibility_structure_v2,
    score_skill_combination_novelty_v2,
)
from app.emergence.emergence_v2 import CandidateMarketStats  # noqa: F401


# ── Temporal evidence ──


@dataclass(frozen=True)
class TemporalLayers:
    """Explicit temporal evidence layers for one candidate/occupation unit."""

    re_observation_persistence: dict[str, Any]
    independent_posting_persistence: dict[str, Any]
    enterprise_diffusion: dict[str, Any]
    source_diffusion: dict[str, Any]
    structural_evolution: dict[str, Any]
    market_growth: dict[str, Any]
    evidence_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "re_observation_persistence": self.re_observation_persistence,
            "independent_posting_persistence": self.independent_posting_persistence,
            "enterprise_diffusion": self.enterprise_diffusion,
            "source_diffusion": self.source_diffusion,
            "structural_evolution": self.structural_evolution,
            "market_growth": self.market_growth,
            "evidence_refs": list(self.evidence_refs),
        }


def _no_growth_reason() -> str:
    return (
        "single independent posting; repeated re-observations of the same "
        "evidence identity do not count as market growth"
    )


def build_candidate_temporal_layers(
    *,
    evidence_identity: str,
    observations: Sequence[Mapping[str, Any]],
) -> TemporalLayers:
    """Candidate-level layers for a single evidence identity (JD posting)."""
    dates = sorted({str(item.get("date") or "") for item in observations if item.get("date")})
    hashes = sorted(
        {str(item.get("content_hash") or "") for item in observations if item.get("content_hash")}
    )
    bundles = sorted(
        {str(item.get("bundle_id") or "") for item in observations if item.get("bundle_id")}
    )
    refs = tuple([evidence_identity, *bundles, *hashes])
    return TemporalLayers(
        re_observation_persistence={
            "dates": dates,
            "distinct_dates": len(dates),
            "observation_count": len(observations),
            "same_content_hash": len(hashes) <= 1,
        },
        independent_posting_persistence={
            "independent_posting_count": 1,
            "distinct_dates": len(dates),
            "evidence_refs": [evidence_identity],
            "note": "single evidence identity; re-observations are not independent postings",
        },
        enterprise_diffusion={
            "independent_enterprise_count": 1,
            "evidence_refs": [evidence_identity],
            "note": "single evidence identity provides at most one enterprise observation",
        },
        source_diffusion={
            "independent_source_count": 1,
            "evidence_refs": [evidence_identity],
            "note": "single evidence identity provides at most one source observation",
        },
        structural_evolution={
            "content_hash_count": len(hashes),
            "changed": len(hashes) > 1,
            "content_hashes": hashes,
        },
        market_growth={
            "available": False,
            "reason": _no_growth_reason(),
            "distinct_independent_postings_per_window": [],
        },
        evidence_refs=refs,
    )


def build_cluster_temporal_layers(
    *,
    cluster_key: str,
    members: Sequence[Mapping[str, Any]],
) -> TemporalLayers:
    """Occupation-cluster layers over distinct evidence identities."""
    by_identity: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    enterprises: set[str] = set()
    sources: set[str] = set()
    hashes: set[str] = set()
    dates: set[str] = set()
    for item in members:
        identity = str(item.get("source_record_id") or "")
        if identity:
            by_identity[identity].append(item)
        if item.get("company"):
            enterprises.add(str(item["company"]))
        if item.get("platform"):
            sources.add(str(item["platform"]))
        if item.get("content_hash"):
            hashes.add(str(item["content_hash"]))
        if item.get("date"):
            dates.add(str(item["date"]))
    identities = sorted(by_identity)
    window_counts: Counter[str] = Counter()
    for identity, obs in by_identity.items():
        for item in obs:
            window_counts[str(item.get("date") or "")] += 1
    ordered_dates = sorted(dates)
    growth_available = len(ordered_dates) >= 2
    growth = 0.0
    if growth_available:
        midpoint = len(ordered_dates) // 2
        first = sum(window_counts[d] for d in ordered_dates[:midpoint])
        second = sum(window_counts[d] for d in ordered_dates[midpoint:])
        growth = (
            math.log1p(second) - math.log1p(first)
        ) / max(math.log1p(max(second + first, 1)), 0.01)
    return TemporalLayers(
        re_observation_persistence={
            "dates": sorted(ordered_dates),
            "distinct_dates": len(ordered_dates),
            "observation_count": len(members),
            "same_content_hash": len(hashes) <= 1,
        },
        independent_posting_persistence={
            "independent_posting_count": len(identities),
            "distinct_dates": len(ordered_dates),
            "evidence_refs": identities,
            "note": "distinct evidence identities observed in the cluster",
        },
        enterprise_diffusion={
            "independent_enterprise_count": len(enterprises),
            "evidence_refs": sorted(enterprises),
            "note": "distinct companies in the cluster",
        },
        source_diffusion={
            "independent_source_count": len(sources),
            "evidence_refs": sorted(sources),
            "note": "distinct platforms in the cluster",
        },
        structural_evolution={
            "content_hash_count": len(hashes),
            "changed": len(hashes) > 1,
            "content_hashes": sorted(hashes),
        },
        market_growth={
            "available": growth_available and len(identities) >= 2,
            "growth_delta": round(growth, 6),
            "distinct_independent_postings_per_window": [
                {"date": date, "distinct_postings": window_counts[date]}
                for date in ordered_dates
            ],
            "reason": (
                _no_growth_reason()
                if len(identities) < 2
                else "cluster-level growth over distinct postings per window"
            ),
        },
        evidence_refs=tuple([cluster_key, *identities[:20]]),
    )


def candidate_independent_market_stats(
    *,
    sample: Mapping[str, Any],
    observations: Sequence[Mapping[str, Any]],
    window_order: Sequence[str],
) -> CandidateMarketStats:
    """Market stats that treat re-observations as a single independent posting.

    A single evidence identity yields exactly one window even when the same JD
    was re-captured on multiple dates, so growth stays unavailable and
    diffusion stays candidate-local.
    """
    dates = sorted({str(item.get("date") or "") for item in observations if item.get("date")})
    if not dates:
        return CandidateMarketStats(
            candidate_id=str(sample.get("sample_id") or ""),
            window_counts=(),
            enterprises=(),
            regions=(),
            sources=(),
        )
    latest_date = dates[-1]
    latest_items = [item for item in observations if str(item.get("date") or "") == latest_date]
    return CandidateMarketStats(
        candidate_id=str(sample.get("sample_id") or ""),
        window_counts=((latest_date, 1),),
        enterprises=tuple(
            sorted({str(item.get("company") or "") for item in latest_items if item.get("company")})
        ),
        regions=tuple(
            sorted({str(item.get("region") or "") for item in latest_items if item.get("region")})
        ),
        sources=tuple(
            str(item.get("platform") or "")
            for item in latest_items
            if item.get("platform")
        ),
    )


# ── Occupation clustering ──

_ROLE_SUFFIXES = (
    "工程师",
    "专家",
    "开发",
    "研发",
    "实习生",
    "算法工程师",
    "开发工程师",
    "研发工程师",
    "算法专家",
    "开发专家",
    "岗",
)
_PAREN_RE = re.compile(r"[（(][^）)]*[）)]")


def occupation_key(title: str) -> str:
    """Deterministic occupation cluster key from a job title."""
    text = unicodedata.normalize("NFKC", title or "").casefold()
    text = _PAREN_RE.sub("", text)
    text = re.sub(r"[\s\-_·｜|/\\:：,，。.]+", "", text)
    for suffix in _ROLE_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return text or unicodedata.normalize("NFKC", title or "").casefold()


# ── Mature reference bank ──

_FAMILY_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("SEARCH", ("搜索", "检索")),
    ("RECOMMENDATION", ("推荐",)),
    ("AD", ("广告", "投放", "竞价", "增长算法")),
    ("ROBOTICS", ("机器人", "具身", "embodied", "vla", "运控", "机械臂")),
    ("AUTONOMOUS_DRIVING", ("自动驾驶", "智能驾驶", "辅助驾驶", "adas", "端到端")),
    ("AGENT", ("agent", "智能体", "多智能体")),
    ("LLM", ("大模型", "llm", "aigc", "生成式", "预训练", "微调", "推理", "压缩", "对齐")),
    (
        "ML_NLP_CV",
        (
            "机器学习",
            "深度学习",
            "nlp",
            "自然语言",
            "计算机视觉",
            "cv",
            "多模态",
            "语音",
            "预测",
            "风控",
            "算法",
        ),
    ),
    ("PLATFORM", ("平台", "中间件", "infra", "基础设施", "全栈", "大数据", "数据工程")),
    ("BACKEND", ("后端", "java", "go", "golang", "服务端", "spring")),
    ("EMBEDDED", ("嵌入式", "os", "内核", "驱动")),
    ("OTHER", ()),
)


def family_for_title(title: str) -> str:
    lowered = unicodedata.normalize("NFKC", title or "").casefold()
    for family, patterns in _FAMILY_PATTERNS:
        if any(pattern in lowered for pattern in patterns):
            return family
    return "OTHER"


@dataclass(frozen=True)
class ReferenceProfile:
    family_id: str
    canonical_title: str
    titles: tuple[str, ...]
    skills: tuple[str, ...]
    responsibilities: tuple[str, ...]
    member_document_ids: tuple[str, ...]
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "canonical_title": self.canonical_title,
            "titles": list(self.titles[:10]),
            "skills": list(self.skills[:15]),
            "responsibilities": list(self.responsibilities[:10]),
            "member_count": len(self.member_document_ids),
            "source": self.source,
        }


def build_mature_reference_bank(
    *,
    documents: Sequence[Mapping[str, Any]],
    formal_llm: Mapping[str, Sequence[str]] | None = None,
    formal_backend: Mapping[str, Sequence[str]] | None = None,
    min_family_size: int = 5,
    enrichment: Mapping[str, Mapping[str, Sequence[str]]] | None = None,
) -> list[ReferenceProfile]:
    """Build mature occupation reference profiles from frozen bundle JDs.

    ``documents`` items carry ``job_title``, ``skills`` (raw skill quotes) and
    ``responsibilities`` (responsibility quotes).  Formal LLM/backend profiles
    are appended when provided (frozen position references).
    """
    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for doc in documents:
        title = str(doc.get("job_title") or "")
        if not title:
            continue
        by_family[family_for_title(title)].append(doc)
    profiles: list[ReferenceProfile] = []
    for family, docs in sorted(by_family.items()):
        if len(docs) < min_family_size:
            continue
        titles = Counter(str(d.get("job_title") or "") for d in docs)
        skill_counter: Counter[str] = Counter()
        resp_counter: Counter[str] = Counter()
        enriched_resp: Counter[str] = Counter()
        skill_doc_support: Counter[str] = Counter()
        for doc in docs:
            seen_skills: set[str] = set()
            for skill in doc.get("normalized_skill_names") or ():
                key = str(skill).strip()
                if key:
                    skill_counter[key] += 1
                    seen_skills.add(key)
            for skill in doc.get("skills") or ():
                key = str(skill).strip()
                if key:
                    skill_counter[key] += 1
                    seen_skills.add(key)
            for key in seen_skills:
                skill_doc_support[key] += 1
            for resp in doc.get("responsibilities") or ():
                resp_counter[str(resp).strip()] += 1
            doc_id = str(doc.get("document_id") or "")
            enriched = (enrichment or {}).get(doc_id)
            if enriched:
                for skill in enriched.get("skills") or ():
                    skill_counter[str(skill).strip()] += 2
                for resp in enriched.get("responsibilities") or ():
                    enriched_resp[str(resp).strip()] += 1
        # 10% doc support keeps family core skills meaningful for diverse
        # algorithm families (search/ad/agent/LLM) while dropping rare noise.
        min_support = max(2, round(0.10 * len(docs)))
        core_skills = [
            skill
            for skill, _ in skill_counter.most_common(30)
            if skill_doc_support[skill] >= min_support
        ][:15]
        responsibilities = (
            tuple(r for r, _ in enriched_resp.most_common(10))
            if enriched_resp
            else tuple(r for r, _ in resp_counter.most_common(10))
        )
        profiles.append(
            ReferenceProfile(
                family_id=family,
                canonical_title=titles.most_common(1)[0][0],
                titles=tuple(t for t, _ in titles.most_common(10)),
                skills=tuple(core_skills),
                responsibilities=responsibilities,
                member_document_ids=tuple(
                    str(d.get("document_id") or "") for d in docs[:50]
                ),
                source="frozen-bundle-jds+normalized-extraction",
            )
        )
    if formal_llm:
        profiles.append(
            ReferenceProfile(
                family_id="LLM_ALGORITHM_ENGINEER",
                canonical_title="大模型算法工程师",
                titles=("大模型算法工程师",),
                skills=tuple(formal_llm.get("skills", ())),
                responsibilities=tuple(formal_llm.get("responsibilities", ())),
                member_document_ids=(),
                source="formal-position-reference",
            )
        )
    if formal_backend:
        profiles.append(
            ReferenceProfile(
                family_id="BACKEND_ENGINEER",
                canonical_title="后端开发工程师",
                titles=("后端开发工程师",),
                skills=tuple(formal_backend.get("skills", ())),
                responsibilities=tuple(formal_backend.get("responsibilities", ())),
                member_document_ids=(),
                source="formal-position-reference",
            )
        )
    return profiles


def _text_similarity(left: str, right: str) -> float:
    left_tokens = set(_tokenize(left))
    right_tokens = set(_tokenize(right))
    union = left_tokens | right_tokens
    return len(left_tokens & right_tokens) / len(union) if union else 0.0


_CJK_RE = r"[㐀-䶿一-鿿豈-﫿]"
_RUN_RE = re.compile(rf"[0-9a-z]+|{_CJK_RE}+")


def _tokenize(text: str) -> frozenset[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens: set[str] = set()
    for run in _RUN_RE.findall(normalized):
        if run[0].isascii():
            tokens.add(run)
        else:
            if len(run) <= 1:
                tokens.add(run)
            else:
                tokens.update(run[i : i + 2] for i in range(len(run) - 1))
    return frozenset(tokens)


def _skill_ids_from_quotes(quotes: Sequence[str], skill_index: Any) -> set[str]:
    ids: set[str] = set()
    raw_keys = list(getattr(skill_index, "raw_map", {}).keys())
    for quote in quotes:
        folded = unicodedata.normalize("NFKC", quote).casefold()
        for raw in raw_keys:
            raw_folded = unicodedata.normalize("NFKC", raw).casefold()
            if raw_folded and raw_folded in folded:
                info = skill_index.resolve(raw)
                if info.resolved:
                    ids.add(info.skill_id)
    return ids


def retrieve_top_k(
    *,
    candidate_title: str,
    candidate_skills: Sequence[str],
    candidate_responsibilities: Sequence[str],
    bank: Sequence[ReferenceProfile],
    skill_index: Any,
    encoder: Callable[[str], Sequence[float]] | None,
    exclude_document_ids: Sequence[str] = (),
    k: int = 3,
) -> list[dict[str, Any]]:
    """Rank mature references by title + skill structure + responsibility embedding."""
    excluded = set(exclude_document_ids)
    candidate_skill_ids = _skill_ids_from_quotes(candidate_skills, skill_index)
    candidate_resp_vectors = (
        [encoder(resp) for resp in candidate_responsibilities] if encoder is not None else []
    )
    scored: list[dict[str, Any]] = []
    for profile in bank:
        if excluded & set(profile.member_document_ids):
            continue
        title_sim = max(
            (_text_similarity(candidate_title, title) for title in profile.titles),
            default=0.0,
        )
        ref_skill_ids = _skill_ids_from_quotes(profile.skills, skill_index)
        union = candidate_skill_ids | ref_skill_ids
        skill_sim = (
            len(candidate_skill_ids & ref_skill_ids) / len(union) if union else 0.0
        )
        resp_sim = 0.0
        if encoder is not None and candidate_resp_vectors and profile.responsibilities:
            ref_vectors = [encoder(resp) for resp in profile.responsibilities[:10]]
            sims = [
                _cosine(cv, rv)
                for cv in candidate_resp_vectors
                for rv in ref_vectors
            ]
            resp_sim = max(sims, default=0.0)
        combined = 0.40 * title_sim + 0.35 * skill_sim + 0.25 * resp_sim
        scored.append(
            {
                "family_id": profile.family_id,
                "canonical_title": profile.canonical_title,
                "combined_score": round(combined, 6),
                "title_similarity": round(title_sim, 6),
                "skill_similarity": round(skill_sim, 6),
                "responsibility_similarity": round(resp_sim, 6),
                "member_count": len(profile.member_document_ids),
                "source": profile.source,
                "profile": profile,
            }
        )
    scored.sort(key=lambda item: (-item["combined_score"], item["family_id"]))
    result = []
    for item in scored[:k]:
        item.pop("profile")
        result.append(item)
    return result


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    norm_l = math.sqrt(sum(v * v for v in left)) or 1.0
    norm_r = math.sqrt(sum(v * v for v in right)) or 1.0
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    return max(0.0, min(1.0, dot / (norm_l * norm_r)))


def explain_relation_with_reference(
    *,
    title: str,
    skills: Sequence[str],
    responsibilities: Sequence[str],
    reference: ReferenceProfile,
    skill_index: Any,
    encoder: EmbeddingSemanticEncoder | None,
    domain_keywords: Mapping[str, frozenset[str]],
    allow_reference_override: bool = True,
    override_combined_min: float = 0.50,
) -> dict[str, Any]:
    """Run v2.1 relation policy with the mature reference as the baseline.

    ``allow_reference_override`` keeps the generic baseline free of the
    mature-reference override; ``override_combined_min`` is the explanation
    strength at which a mature reference may override an ``unexplained``
    result (default 0.50; the top-k ranking path uses its own 0.45 bar so
    that "a reference explains the candidate" has one consistent meaning).
    """
    resolved = skill_index.resolve_many(tuple(skills))
    ref_resolved = skill_index.resolve_many(tuple(reference.skills))
    name_dim = score_name_novelty_v2(
        title,
        baseline_titles=reference.titles[:5],
        canonical_title=reference.canonical_title,
        taxonomy_version="position-taxonomy.v3.0.0",
    )
    skill_dim = score_skill_combination_novelty_v2(
        resolved,
        before_skills=ref_resolved,
    )
    resp_dim = score_responsibility_structure_v2(
        tuple(responsibilities),
        before_responsibilities=reference.responsibilities,
        encoder=encoder,
    )
    claimed_domains = sorted(
        set(skill_dim.components.get("added_domains", ()) or ())
        | set(skill_dim.components.get("added_structural_skill_domains", ()) or ())
    )
    domain_evidence = responsibility_domain_evidence(
        tuple(responsibilities),
        domain_keywords,
        [d for d in claimed_domains if d in domain_keywords],
    )
    relation = classify_relation_v2(
        name_dim=name_dim,
        skill_dim=skill_dim,
        resp_dim=resp_dim,
        baseline_source="mature_reference",
        taxonomy_available=True,
        domain_evidence=domain_evidence,
    )
    title_sim = 1.0 - (name_dim.score or 0.0)
    skill_sim = float(
        skill_dim.components.get("relevant_baseline_retained_ratio", 0.0)
    ) or float(skill_dim.components.get("retained_core_ratio", 0.0))
    resp_sim = max(
        (
            float(item.get("best_similarity") or 0.0)
            for item in resp_dim.components.get("pairwise_alignment", ()) or ()
        ),
        default=0.0,
    )
    combined = 0.40 * title_sim + 0.35 * skill_sim + 0.25 * resp_sim
    explained_by_reference = False
    explained_relation = relation.relation
    if relation.relation == "unexplained_structural_novelty" and allow_reference_override:
        explained_by_reference = (
            combined >= override_combined_min
            and (title_sim >= 0.40 or skill_sim >= 0.35 or resp_sim >= 0.60)
        )
        if explained_by_reference:
            added_structural = len(
                skill_dim.components.get("added_structural_skills", ()) or ()
            )
            added_subdomains = set(
                skill_dim.components.get("structural_added_subdomains", ()) or ()
            )
            added_domains = set(skill_dim.components.get("added_domains", ()) or ())
            if len(added_subdomains) == 1 and len(added_domains) <= 1:
                explained_relation = "specialization"
            elif added_structural == 0:
                explained_relation = "same_or_not_novel"
            else:
                explained_relation = "renaming"
            relation = RelationClassification(
                relation=explained_relation,
                confidence=round(relation.confidence * 0.9, 6),
                reason=(
                    "explained by mature reference "
                    f"{reference.family_id} (combined={combined:.3f}); "
                    "structural-novelty overridden by mature-reference explanation"
                ),
                components=relation.components,
            )
    return {
        "reference_family": reference.family_id,
        "reference_canonical_title": reference.canonical_title,
        "reference_source": reference.source,
        "relation": relation.relation,
        "relation_confidence": round(relation.confidence, 6),
        "relation_reason": relation.reason,
        "explained_by_reference": explained_by_reference,
        "explained_relation": explained_relation,
        "explanation_components": {
            "title_similarity": round(title_sim, 6),
            "skill_similarity": round(skill_sim, 6),
            "responsibility_similarity": round(resp_sim, 6),
            "combined": round(combined, 6),
        },
        "skill_dim_evidence": skill_dim,
        "dimensions": {
            "name_novelty": name_dim.to_dict(),
            "skill_combination_novelty": skill_dim.to_dict(),
            "responsibility_structure_novelty": resp_dim.to_dict(),
        },
    }


_EXPLANATORY_RELATIONS = {
    "same_or_not_novel",
    "renaming",
    "specialization",
    "hybridization",
    "tool_shift",
}


def explain_with_reference_ranking(
    *,
    title: str,
    skills: Sequence[str],
    responsibilities: Sequence[str],
    top_k: Sequence[Mapping[str, Any]],
    bank_by_family: Mapping[str, ReferenceProfile],
    skill_index: Any,
    encoder: EmbeddingSemanticEncoder | None,
    domain_keywords: Mapping[str, frozenset[str]],
    min_explain_combined: float = 0.45,
) -> dict[str, Any]:
    """Explain a candidate against top-k mature references.

    A candidate is explained whenever any top-k reference yields a real
    explanatory relation (same/renaming/specialization/hybridization/tool_shift)
    with a reasonably strong combined explanation.  The highest-ranked
    qualifying reference is chosen.  Only when every top-k reference fails the
    explanation bar does the result keep the v2.1 relation of the best match
    (``insufficient_evidence`` or ``unexplained_structural_novelty``).
    """
    attempts: list[dict[str, Any]] = []
    qualifiers: list[dict[str, Any]] = []
    for rank, item in enumerate(top_k, start=1):
        reference = bank_by_family.get(str(item.get("family_id") or ""))
        if reference is None:
            continue
        explanation = explain_relation_with_reference(
            title=title,
            skills=skills,
            responsibilities=responsibilities,
            reference=reference,
            skill_index=skill_index,
            encoder=encoder,
            domain_keywords=domain_keywords,
            allow_reference_override=True,
            override_combined_min=min_explain_combined,
        )
        explanation["reference_rank"] = rank
        attempts.append(explanation)
        combined = float(explanation["explanation_components"]["combined"])
        if explanation["relation"] in _EXPLANATORY_RELATIONS and combined >= min_explain_combined:
            qualifiers.append(explanation)
    if qualifiers:
        chosen = min(qualifiers, key=lambda item: (item["reference_rank"], -float(item["explanation_components"]["combined"])))
        return {**chosen, "chosen_from_ranking": True, "candidate_references_tried": len(attempts)}
    if attempts:
        chosen = attempts[0]
        return {**chosen, "chosen_from_ranking": False, "candidate_references_tried": len(attempts)}
    return {
        "reference_family": "NONE",
        "reference_canonical_title": "",
        "reference_source": "",
        "relation": "insufficient_evidence",
        "relation_confidence": 0.0,
        "relation_reason": "NO_REFERENCE: no mature reference available",
        "explained_by_reference": False,
        "explained_relation": "insufficient_evidence",
        "explanation_components": {
            "title_similarity": 0.0,
            "skill_similarity": 0.0,
            "responsibility_similarity": 0.0,
            "combined": 0.0,
        },
        "reference_rank": 0,
        "chosen_from_ranking": False,
        "candidate_references_tried": 0,
    }


__all__ = [
    "CandidateMarketStats",
    "LexicalFallbackEncoder",
    "ReferenceProfile",
    "TemporalLayers",
    "build_candidate_temporal_layers",
    "build_cluster_temporal_layers",
    "build_mature_reference_bank",
    "candidate_independent_market_stats",
    "classify_emergence_v2",
    "explain_relation_with_reference",
    "explain_with_reference_ranking",
    "family_for_title",
    "map_to_v1_class",
    "occupation_key",
    "retrieve_top_k",
    "score_market_behavior_v2",
]
