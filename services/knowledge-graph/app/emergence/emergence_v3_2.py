"""EXP-EMERGE-01 v3.2 Stage2 occupation-cluster acceptance.

Stage2 evaluation unit moves from a single evidence identity / D5 candidate
to a cross-JD occupation cluster (JD = observation, cluster = analysis unit).

The six v3.1 temporal layers are reused:
  - re_observation_persistence (same JD / same content hash re-captured)
  - independent_posting_persistence (distinct evidence identities)
  - enterprise / source diffusion
  - structural evolution (content changed across windows)
  - market growth (distinct postings / enterprises per window)

``re_observation_persistence`` is never a positive emerging signal and
family-level market context is never used at cluster level.

``emerging`` requires:
  - Stage1 structural signal (specialization / hybridization /
    unexplained_structural_novelty),
  - independent posting persistence (>=2 distinct JDs across >=2 dates),
  - at least one real diffusion (enterprise preferred; source may assist),
  - temporal persistence / growth or structural evolution,
  - evidence sufficiency (Stage1 evaluated and temporal evidence present).

The observed window is 12 days / 6 dates, so ``emerging`` results carry
``evidence_level="short_window"`` and never claim long-term market growth.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.emergence.emergence_v3_1 import _cosine, _skill_ids_from_quotes, _text_similarity

EXPLAINABLE_STRUCTURAL_RELATIONS = frozenset({"specialization", "hybridization"})
UNEXPLAINED_RELATION = "unexplained_structural_novelty"
STRUCTURAL_RELATIONS = EXPLAINABLE_STRUCTURAL_RELATIONS | frozenset(
    {UNEXPLAINED_RELATION}
)
STABLE_RELATIONS = frozenset({"same_or_not_novel", "renaming", "tool_shift"})

# Stage2 reliability bar for treating ``unexplained_structural_novelty`` as a
# structural signal: the role must genuinely fail mature-reference
# explanation (weak retrieval combined < 0.45), the attempted reference must
# be a real family with core skills (not the empty OTHER catch-all), and the
# reference must be in the same skill domain as the candidate AND the
# candidate must inherit part of the reference core (skill_similarity > 0).
# Otherwise the "unexplained" result is a bank-coverage artifact, not
# reliable structural novelty.
UNEXPLAINED_COMBINED_MAX = 0.45


# ── cached retrieval (semantically identical to v3.1 retrieve_top_k) ──

_PROFILE_SKILL_ID_CACHE: dict[int, frozenset[str]] = {}
_QUOTE_SKILL_ID_CACHE: dict[tuple[int, str], frozenset[str]] = {}


def _profile_skill_ids(profile: Any, skill_index: Any) -> frozenset[str]:
    key = id(profile)
    if key not in _PROFILE_SKILL_ID_CACHE:
        _PROFILE_SKILL_ID_CACHE[key] = frozenset(
            _skill_ids_from_quotes(profile.skills, skill_index)
        )
    return _PROFILE_SKILL_ID_CACHE[key]


def _quote_skill_ids(quote: str, skill_index: Any) -> frozenset[str]:
    key = (id(skill_index), quote)
    if key not in _QUOTE_SKILL_ID_CACHE:
        _QUOTE_SKILL_ID_CACHE[key] = frozenset(
            _skill_ids_from_quotes([quote], skill_index)
        )
    return _QUOTE_SKILL_ID_CACHE[key]


def retrieve_top_k_cached(
    *,
    candidate_title: str,
    candidate_skills: tuple[str, ...],
    candidate_responsibilities: tuple[str, ...],
    bank: list[Any],
    skill_index: Any,
    encoder: Any,
    exclude_document_ids: tuple[str, ...] = (),
    k: int = 3,
) -> list[dict[str, Any]]:
    """Rank mature references; identical scoring to v31.retrieve_top_k."""
    excluded = set(exclude_document_ids)
    candidate_skill_ids: set[str] = set()
    for quote in candidate_skills:
        candidate_skill_ids |= _quote_skill_ids(str(quote), skill_index)
    candidate_resp_vectors = (
        [encoder(resp) for resp in candidate_responsibilities]
        if encoder is not None
        else []
    )
    scored: list[dict[str, Any]] = []
    for profile in bank:
        if excluded & set(profile.member_document_ids):
            continue
        title_sim = max(
            (_text_similarity(candidate_title, title) for title in profile.titles),
            default=0.0,
        )
        ref_skill_ids = _profile_skill_ids(profile, skill_index)
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
            }
        )
    scored.sort(key=lambda item: (-item["combined_score"], item["family_id"]))
    return scored[:k]


def _count(
    layers: Mapping[str, Any],
    layer: str,
    field: str,
) -> int:
    value = (layers.get(layer) or {}).get(field)
    return int(value) if value is not None else 0


def _flag(layers: Mapping[str, Any], layer: str, field: str) -> bool:
    return bool((layers.get(layer) or {}).get(field))


def cluster_stage2_decision(
    *,
    cluster_relation: str,
    layers: Mapping[str, Any],
    min_postings: int = 2,
    min_enterprises: int = 2,
    min_sources: int = 2,
    ablation: str | None = None,
    structural_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Decide the Stage2 state for one occupation cluster.

    ``ablation`` supports the three minimal ablations:
      - "no_temporal": all temporal gates removed (persistence / growth /
        structural evolution ignored);
      - "no_enterprise_diffusion": enterprise gate removed, only source
        diffusion with real independent postings may assist;
      - "no_structural_evolution": content-change evidence ignored.
    """
    relation = str(cluster_relation or "")
    sev = structural_evidence or {}
    independent_count = _count(
        layers, "independent_posting_persistence", "independent_posting_count"
    )
    distinct_dates = _count(
        layers, "independent_posting_persistence", "distinct_dates"
    )
    re_obs_dates = _count(layers, "re_observation_persistence", "distinct_dates")
    enterprise_count = _count(
        layers, "enterprise_diffusion", "independent_enterprise_count"
    )
    source_count = _count(layers, "source_diffusion", "independent_source_count")
    content_hashes = _count(layers, "structural_evolution", "content_hash_count")
    structural_evolution = _flag(layers, "structural_evolution", "changed")
    market_growth_available = _flag(layers, "market_growth", "available")

    structural_details: dict[str, Any] = {"relation": relation}
    if relation in EXPLAINABLE_STRUCTURAL_RELATIONS:
        structural_gate = True
        structural_details["kind"] = "explainable_structural_change"
    elif relation == UNEXPLAINED_RELATION:
        ref_family = str(sev.get("reference_family") or "")
        ref_non_empty = bool(sev.get("reference_core_skills_non_empty"))
        candidate_domains = set(sev.get("candidate_skill_domains") or ())
        ref_domains = set(sev.get("reference_core_domains") or ())
        domain_overlap = bool(candidate_domains & ref_domains)
        core_inherited = bool(sev.get("reference_core_inherited"))
        weak_explanation = (
            float(sev.get("explanation_combined") or 0.0) < UNEXPLAINED_COMBINED_MAX
        )
        structural_gate = (
            ref_family != "OTHER"
            and ref_non_empty
            and domain_overlap
            and core_inherited
            and weak_explanation
        )
        structural_details.update(
            {
                "kind": "unexplained",
                "reference_family": ref_family,
                "reference_core_skills_non_empty": ref_non_empty,
                "domain_overlap": domain_overlap,
                "reference_core_inherited": core_inherited,
                "candidate_skill_domains": sorted(candidate_domains),
                "reference_core_domains": sorted(ref_domains),
                "explanation_combined": float(sev.get("explanation_combined") or 0.0),
                "weak_explanation": weak_explanation,
            }
        )
    else:
        structural_gate = False
        structural_details["kind"] = "stable_or_unavailable"
    persistence_gate = independent_count >= min_postings and distinct_dates >= 2
    enterprise_gate = enterprise_count >= min_enterprises
    source_gate = source_count >= min_sources
    diffusion_gate = enterprise_gate or (
        source_gate and independent_count >= min_postings
    )
    temporal_gate = (
        persistence_gate or structural_evolution or market_growth_available
    )
    any_temporal = (
        re_obs_dates >= 2 or structural_evolution or market_growth_available
    )

    if ablation == "no_temporal":
        persistence_gate = False
        temporal_gate = False
        any_temporal = True  # keep evaluating structure; only temporal gates removed
    elif ablation == "no_enterprise_diffusion":
        enterprise_gate = False
        diffusion_gate = source_gate and independent_count >= min_postings
    elif ablation == "no_structural_evolution":
        structural_evolution = False
        temporal_gate = persistence_gate or market_growth_available

    counts = {
        "independent_postings": independent_count,
        "distinct_dates": distinct_dates,
        "re_observation_dates": re_obs_dates,
        "enterprises": enterprise_count,
        "sources": source_count,
        "content_hash_count": content_hashes,
        "market_growth_available": market_growth_available,
    }
    gates = {
        "structural_signal": structural_gate,
        "structural_signal_details": structural_details,
        "independent_posting_persistence": persistence_gate,
        "enterprise_diffusion": enterprise_gate,
        "source_diffusion": source_gate,
        "diffusion": diffusion_gate,
        "temporal_persistence_growth_or_evolution": temporal_gate,
        "any_temporal_evidence": any_temporal,
    }
    missing = sorted(gate for gate, ok in gates.items() if not ok and gate != "any_temporal_evidence")
    evidence_refs = list(layers.get("evidence_refs") or ())

    if relation == "insufficient_evidence" or not relation:
        return {
            "state": "insufficient_evidence",
            "evidence_level": None,
            "gates": gates,
            "counts": counts,
            "missing_gates": missing,
            "reason": f"Stage1 insufficient/absent: relation={relation or 'NONE'}",
            "evidence_refs": evidence_refs,
            "ablation": ablation or "none",
        }
    if not structural_gate:
        return {
            "state": "not_emerging",
            "evidence_level": None,
            "gates": gates,
            "counts": counts,
            "missing_gates": missing,
            "reason": (
                f"no Stage1 structural signal (relation={relation}); market/"
                "diffusion context alone cannot establish emergence"
            ),
            "evidence_refs": evidence_refs,
            "ablation": ablation or "none",
        }
    if not any_temporal:
        return {
            "state": "insufficient_evidence",
            "evidence_level": None,
            "gates": gates,
            "counts": counts,
            "missing_gates": missing,
            "reason": "structural signal but no temporal evidence at all",
            "evidence_refs": evidence_refs,
            "ablation": ablation or "none",
        }
    if persistence_gate and diffusion_gate and temporal_gate:
        return {
            "state": "emerging",
            "evidence_level": "short_window",
            "gates": gates,
            "counts": counts,
            "missing_gates": missing,
            "reason": (
                "emerging: Stage1 structural signal + independent posting "
                "persistence + diffusion + temporal/structural evidence; "
                "12-day / 6-date window only"
            ),
            "evidence_refs": evidence_refs,
            "ablation": ablation or "none",
        }
    return {
        "state": "weak_emerging_signal",
        "evidence_level": None,
        "gates": gates,
        "counts": counts,
        "missing_gates": missing,
        "reason": (
            "weak: Stage1 structural signal but missing one or more emerging "
            f"gates ({missing})"
        ),
        "evidence_refs": evidence_refs,
        "ablation": ablation or "none",
    }


def cluster_stage2_with_ablations(
    *,
    cluster_relation: str,
    layers: Mapping[str, Any],
    structural_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Baseline decision plus the three minimal ablations."""
    return {
        "baseline": cluster_stage2_decision(
            cluster_relation=cluster_relation,
            layers=layers,
            structural_evidence=structural_evidence,
        ),
        "no_temporal": cluster_stage2_decision(
            cluster_relation=cluster_relation,
            layers=layers,
            ablation="no_temporal",
            structural_evidence=structural_evidence,
        ),
        "no_enterprise_diffusion": cluster_stage2_decision(
            cluster_relation=cluster_relation,
            layers=layers,
            ablation="no_enterprise_diffusion",
            structural_evidence=structural_evidence,
        ),
        "no_structural_evolution": cluster_stage2_decision(
            cluster_relation=cluster_relation,
            layers=layers,
            ablation="no_structural_evolution",
            structural_evidence=structural_evidence,
        ),
    }


__all__ = [
    "STABLE_RELATIONS",
    "STRUCTURAL_RELATIONS",
    "EXPLAINABLE_STRUCTURAL_RELATIONS",
    "UNEXPLAINED_RELATION",
    "UNEXPLAINED_COMBINED_MAX",
    "cluster_stage2_decision",
    "cluster_stage2_with_ablations",
]
