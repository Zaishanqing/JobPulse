"""Candidate Identity Profile v3 (challenger-only).

Profile v3 keeps the v2 separation between recent evidence, stable anchor
evidence, and alias history, but changes three structural properties:

- recent title/responsibility/skills are never averaged away by a fixed
  anchor weight; they remain independently observable signals;
- stable anchor evidence is narrowed to top-K discriminative skills and
  responsibility bigrams, so a long cumulative union cannot make an anchor
  artificially similar;
- low-support profiles gate anchor evidence instead of letting an unstable
  anchor override recent evidence.

The module is deliberately additive and does not change production Identity
v2, Candidate Assignment v2, Gold, Admission, or Lifecycle code.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Mapping, Sequence

from app.domain.candidate_identity import (
    CandidateIdentitySpec,
    _cosine,
    _jaccard,
    _normalise_evidence_text,
    _tokens,
)
from app.domain.candidate_profile import (
    CandidateWindowEvidence,
    _bigrams_cached,
    _fast_text_similarity,
    _title_tokens,
    weighted_jaccard,
    window_evidence_from_samples,
)


PROFILE_V3_VERSION = "candidate-identity-profile.v3"
PROFILE_V3_REBUILD_SCHEMA = "candidate-identity-profile-v3-rebuild.v1"

DEFAULT_PROFILE_V3_CONFIG: dict[str, Any] = {
    "recent_window_count": 2,
    "anchor_support_min_windows": 2,
    "skill_support_min_ratio": 0.25,
    "responsibility_bigram_support_min_ratio": 0.15,
    "anchor_top_k_skills": 24,
    "anchor_top_k_responsibility_bigrams": 32,
    "semantic_ema_alpha": 0.5,
    "skill_weight_floor": 0.0,
    "low_support_anchor_gate": True,
}


@dataclass(frozen=True)
class CandidateIdentityProfileV3:
    """Deterministic, versioned Candidate profile for the v3 challenger."""

    profile_version: str = PROFILE_V3_VERSION
    candidate_id: str | None = None
    recent_window_ids: tuple[str, ...] = ()
    recent_window_evidence: tuple[CandidateWindowEvidence, ...] = ()
    recent_titles: frozenset[str] = frozenset()
    recent_skills: frozenset[str] = frozenset()
    recent_responsibilities: frozenset[str] = frozenset()
    recent_semantic_centroid: tuple[float, ...] = ()
    skill_window_frequency: dict[str, int] = field(default_factory=dict)
    responsibility_window_frequency: dict[str, int] = field(default_factory=dict)
    responsibility_bigram_window_frequency: dict[str, int] = field(
        default_factory=dict
    )
    title_token_window_frequency: dict[str, int] = field(default_factory=dict)
    core_title_tokens: frozenset[str] = frozenset()
    title_alias_history: frozenset[str] = frozenset()
    responsibility_alias_history: frozenset[str] = frozenset()
    semantic_centroid: tuple[float, ...] = ()
    support_window_count: int = 0
    observed_window_ids: tuple[str, ...] = ()
    first_seen_window_id: str | None = None
    last_seen_window_id: str | None = None
    member_evidence_ids: frozenset[str] = frozenset()
    member_dedup_cluster_ids: frozenset[str] = frozenset()
    member_template_cluster_ids: frozenset[str] = frozenset()

    @property
    def skill_support_ratio(self) -> dict[str, float]:
        denominator = self.support_window_count or 1
        return {
            skill: round(frequency / denominator, 6)
            for skill, frequency in sorted(self.skill_window_frequency.items())
        }

    @property
    def responsibility_bigram_support_ratio(self) -> dict[str, float]:
        denominator = self.support_window_count or 1
        return {
            gram: round(frequency / denominator, 6)
            for gram, frequency in sorted(
                self.responsibility_bigram_window_frequency.items()
            )
        }

    def top_skill_anchor_weights(
        self,
        config: Mapping[str, Any] | None = None,
    ) -> dict[str, float]:
        merged = _profile_v3_config(config)
        min_ratio = float(merged["skill_support_min_ratio"])
        top_k = int(merged["anchor_top_k_skills"])
        floor = float(merged["skill_weight_floor"])
        candidates = [
            (ratio, skill)
            for skill, ratio in self.skill_support_ratio.items()
            if ratio >= min_ratio
        ]
        candidates.sort(key=lambda item: (-item[0], item[1]))
        return {
            skill: max(ratio, floor)
            for ratio, skill in candidates[:top_k]
        }

    def top_responsibility_anchor_weights(
        self,
        config: Mapping[str, Any] | None = None,
    ) -> dict[str, float]:
        merged = _profile_v3_config(config)
        min_ratio = float(merged["responsibility_bigram_support_min_ratio"])
        top_k = int(merged["anchor_top_k_responsibility_bigrams"])
        candidates = [
            (ratio, gram)
            for gram, ratio in self.responsibility_bigram_support_ratio.items()
            if ratio >= min_ratio
        ]
        candidates.sort(key=lambda item: (-item[0], item[1]))
        return {gram: ratio for ratio, gram in candidates[:top_k]}

    def anchor_gate(self, config: Mapping[str, Any] | None = None) -> float:
        merged = _profile_v3_config(config)
        if not merged["low_support_anchor_gate"]:
            return 1.0
        min_windows = int(merged["anchor_support_min_windows"])
        if min_windows <= 0:
            return 1.0
        return min(1.0, self.support_window_count / min_windows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_version": self.profile_version,
            "candidate_id": self.candidate_id,
            "recent_profile": {
                "window_ids": list(self.recent_window_ids),
                "titles": sorted(self.recent_titles),
                "skills": sorted(self.recent_skills),
                "responsibilities": sorted(self.recent_responsibilities),
                "semantic_centroid": list(self.recent_semantic_centroid),
            },
            "identity_anchor": {
                "skill_frequency": dict(sorted(self.skill_window_frequency.items())),
                "responsibility_frequency": dict(
                    sorted(self.responsibility_window_frequency.items())
                ),
                "responsibility_bigram_frequency": dict(
                    sorted(self.responsibility_bigram_window_frequency.items())
                ),
                "core_title_tokens": sorted(self.core_title_tokens),
                "semantic_centroid": list(self.semantic_centroid),
                "support_window_count": self.support_window_count,
                "member_evidence_ids": sorted(self.member_evidence_ids),
                "member_dedup_cluster_ids": sorted(self.member_dedup_cluster_ids),
                "member_template_cluster_ids": sorted(
                    self.member_template_cluster_ids
                ),
            },
            "alias_history": {
                "title_aliases": sorted(self.title_alias_history),
                "responsibility_aliases": sorted(
                    self.responsibility_alias_history
                ),
            },
            "observed_window_ids": list(self.observed_window_ids),
            "first_seen_window_id": self.first_seen_window_id,
            "last_seen_window_id": self.last_seen_window_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CandidateIdentityProfileV3":
        recent = value.get("recent_profile") or {}
        anchor = value.get("identity_anchor") or {}
        aliases = value.get("alias_history") or {}
        return cls(
            profile_version=str(value.get("profile_version", PROFILE_V3_VERSION)),
            candidate_id=(
                str(value["candidate_id"])
                if value.get("candidate_id") is not None
                else None
            ),
            recent_window_ids=tuple(str(item) for item in recent.get("window_ids", ())),
            recent_titles=frozenset(str(item) for item in recent.get("titles", ())),
            recent_skills=frozenset(str(item) for item in recent.get("skills", ())),
            recent_responsibilities=frozenset(
                str(item) for item in recent.get("responsibilities", ())
            ),
            recent_semantic_centroid=tuple(
                float(item) for item in recent.get("semantic_centroid", ())
            ),
            skill_window_frequency={
                str(key): int(item)
                for key, item in (anchor.get("skill_frequency") or {}).items()
            },
            responsibility_window_frequency={
                str(key): int(item)
                for key, item in (
                    anchor.get("responsibility_frequency") or {}
                ).items()
            },
            responsibility_bigram_window_frequency={
                str(key): int(item)
                for key, item in (
                    anchor.get("responsibility_bigram_frequency") or {}
                ).items()
            },
            title_token_window_frequency={},
            core_title_tokens=frozenset(
                str(item) for item in anchor.get("core_title_tokens", ())
            ),
            title_alias_history=frozenset(
                str(item) for item in aliases.get("title_aliases", ())
            ),
            responsibility_alias_history=frozenset(
                str(item) for item in aliases.get("responsibility_aliases", ())
            ),
            semantic_centroid=tuple(
                float(item) for item in anchor.get("semantic_centroid", ())
            ),
            support_window_count=int(anchor.get("support_window_count", 0)),
            observed_window_ids=tuple(
                str(item) for item in value.get("observed_window_ids", ())
            ),
            first_seen_window_id=(
                str(value["first_seen_window_id"])
                if value.get("first_seen_window_id") is not None
                else None
            ),
            last_seen_window_id=(
                str(value["last_seen_window_id"])
                if value.get("last_seen_window_id") is not None
                else None
            ),
            member_evidence_ids=frozenset(
                str(item) for item in anchor.get("member_evidence_ids", ())
            ),
            member_dedup_cluster_ids=frozenset(
                str(item) for item in anchor.get("member_dedup_cluster_ids", ())
            ),
            member_template_cluster_ids=frozenset(
                str(item) for item in anchor.get("member_template_cluster_ids", ())
            ),
        )


def _profile_v3_config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    merged = {**DEFAULT_PROFILE_V3_CONFIG, **(config or {})}
    recent_count = int(merged["recent_window_count"])
    if recent_count < 1:
        raise ValueError("profile v3 recent_window_count must be positive")
    if int(merged["anchor_support_min_windows"]) < 0:
        raise ValueError("profile v3 anchor_support_min_windows must be non-negative")
    if int(merged["anchor_top_k_skills"]) < 1:
        raise ValueError("profile v3 anchor_top_k_skills must be positive")
    if int(merged["anchor_top_k_responsibility_bigrams"]) < 1:
        raise ValueError(
            "profile v3 anchor_top_k_responsibility_bigrams must be positive"
        )
    for key in (
        "skill_support_min_ratio",
        "responsibility_bigram_support_min_ratio",
        "semantic_ema_alpha",
        "skill_weight_floor",
    ):
        if float(merged[key]) < 0:
            raise ValueError(f"profile v3 {key} must be non-negative")
    if not 0 <= float(merged["semantic_ema_alpha"]) <= 1:
        raise ValueError("profile v3 semantic EMA alpha must be between zero and one")
    return merged


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def profile_v3_config_version(config: Mapping[str, Any] | None = None) -> str:
    merged = _profile_v3_config(config)
    digest = sha256(_canonical_json(merged).encode("utf-8")).hexdigest()[:16]
    return f"{PROFILE_V3_VERSION}/sha256:{digest}"


def empty_profile_v3(candidate_id: str | None = None) -> CandidateIdentityProfileV3:
    return CandidateIdentityProfileV3(candidate_id=candidate_id)


def _ema(
    previous: tuple[float, ...],
    current: tuple[float, ...],
    alpha: float,
) -> tuple[float, ...]:
    if not current:
        return previous
    if not previous:
        return current
    if len(previous) != len(current):
        return current
    return tuple(
        round(alpha * right + (1.0 - alpha) * left, 6)
        for left, right in zip(previous, current, strict=True)
    )


def _recent_evidence(
    evidence: tuple[CandidateWindowEvidence, ...],
    recent_count: int,
) -> tuple[CandidateWindowEvidence, ...]:
    return tuple(evidence[-recent_count:])


def append_window_evidence(
    profile: CandidateIdentityProfileV3,
    evidence: CandidateWindowEvidence,
    config: Mapping[str, Any] | None = None,
) -> CandidateIdentityProfileV3:
    merged = _profile_v3_config(config)
    recent_count = int(merged["recent_window_count"])
    observed = tuple(
        dict.fromkeys((*profile.observed_window_ids, evidence.window_id))
    )
    recent_window_evidence = _recent_evidence(
        (*profile.recent_window_evidence, evidence),
        recent_count,
    )
    recent_window_ids = tuple(item.window_id for item in recent_window_evidence)
    recent_titles = frozenset(
        title for item in recent_window_evidence for title in item.titles
    )
    recent_skills = frozenset(
        skill for item in recent_window_evidence for skill in item.skills
    )
    recent_responsibilities = frozenset(
        text
        for item in recent_window_evidence
        for text in item.responsibilities
    )
    recent_semantic = tuple(
        item.semantic_centroid
        for item in reversed(recent_window_evidence)
        if item.semantic_centroid
    )
    recent_semantic_centroid = recent_semantic[0] if recent_semantic else ()

    skill_frequency = dict(profile.skill_window_frequency)
    for skill in evidence.skills:
        skill_frequency[str(skill)] = skill_frequency.get(str(skill), 0) + 1

    responsibility_frequency = dict(profile.responsibility_window_frequency)
    responsibility_grams: set[str] = set()
    for text in evidence.responsibilities:
        responsibility_frequency[str(text)] = (
            responsibility_frequency.get(str(text), 0) + 1
        )
        responsibility_grams.update(_bigrams_cached(str(text)))

    responsibility_bigram_frequency = dict(
        profile.responsibility_bigram_window_frequency
    )
    for gram in sorted(responsibility_grams):
        responsibility_bigram_frequency[gram] = (
            responsibility_bigram_frequency.get(gram, 0) + 1
        )

    title_token_frequency = dict(profile.title_token_window_frequency)
    window_title_tokens: set[str] = set()
    for title in evidence.titles:
        window_title_tokens.update(_tokens(str(title)))
    for token in sorted(window_title_tokens):
        title_token_frequency[token] = title_token_frequency.get(token, 0) + 1

    support_window_count = profile.support_window_count + 1
    min_support = min(
        int(merged["anchor_support_min_windows"]),
        support_window_count,
    )
    core_title_tokens = frozenset(
        token
        for token, frequency in title_token_frequency.items()
        if frequency >= min_support
    )
    if not core_title_tokens and recent_titles:
        core_title_tokens = frozenset(
            token for title in recent_titles for token in _tokens(title)
        )

    semantic_centroid = _ema(
        profile.semantic_centroid,
        evidence.semantic_centroid,
        float(merged["semantic_ema_alpha"]),
    )
    return CandidateIdentityProfileV3(
        profile_version=PROFILE_V3_VERSION,
        candidate_id=profile.candidate_id,
        recent_window_ids=recent_window_ids,
        recent_window_evidence=recent_window_evidence,
        recent_titles=recent_titles,
        recent_skills=recent_skills,
        recent_responsibilities=recent_responsibilities,
        recent_semantic_centroid=recent_semantic_centroid,
        skill_window_frequency=skill_frequency,
        responsibility_window_frequency=responsibility_frequency,
        responsibility_bigram_window_frequency=responsibility_bigram_frequency,
        title_token_window_frequency=title_token_frequency,
        core_title_tokens=core_title_tokens,
        title_alias_history=frozenset(
            (*profile.title_alias_history, *evidence.titles)
        ),
        responsibility_alias_history=frozenset(
            (*profile.responsibility_alias_history, *evidence.responsibilities)
        ),
        semantic_centroid=semantic_centroid,
        support_window_count=support_window_count,
        observed_window_ids=observed,
        first_seen_window_id=profile.first_seen_window_id or evidence.window_id,
        last_seen_window_id=evidence.window_id,
        member_evidence_ids=frozenset(
            (*profile.member_evidence_ids, *evidence.member_evidence_ids)
        ),
        member_dedup_cluster_ids=frozenset(
            (*profile.member_dedup_cluster_ids, *evidence.member_dedup_cluster_ids)
        ),
        member_template_cluster_ids=frozenset(
            (*profile.member_template_cluster_ids, *evidence.member_template_cluster_ids)
        ),
    )


def build_profile_v3(
    candidate_id: str | None,
    windows: Sequence[CandidateWindowEvidence],
    config: Mapping[str, Any] | None = None,
    *,
    window_order: Sequence[str] | None = None,
) -> CandidateIdentityProfileV3:
    ordered = list(windows)
    if window_order is not None:
        order = {window_id: index for index, window_id in enumerate(window_order)}
        ordered.sort(
            key=lambda item: (
                order.get(item.window_id, len(order)),
                item.window_id,
            )
        )
    profile = empty_profile_v3(candidate_id)
    for evidence in ordered:
        profile = append_window_evidence(profile, evidence, config)
    return profile


def rebuild_profile_v3_from_observations(
    candidate_id: str,
    observations: Sequence[tuple[str, Sequence[str]] | CandidateWindowEvidence],
    samples_by_id: Mapping[str, Mapping[str, Any]],
    config: Mapping[str, Any] | None = None,
    *,
    window_order: Sequence[str] | None = None,
) -> CandidateIdentityProfileV3:
    windows: list[CandidateWindowEvidence] = []
    for observation in observations:
        if isinstance(observation, CandidateWindowEvidence):
            windows.append(observation)
        else:
            window_id, sample_ids = observation
            windows.append(
                window_evidence_from_samples(
                    str(window_id),
                    tuple(str(item) for item in sample_ids),
                    samples_by_id,
                )
            )
    return build_profile_v3(
        candidate_id,
        windows,
        config,
        window_order=window_order,
    )


def _current_bigram_weights(
    responsibilities: Sequence[str] | frozenset[str],
) -> dict[str, float]:
    weights: Counter[str] = Counter()
    for text in responsibilities:
        for gram in _bigrams_cached(str(text)):
            weights[gram] = 1.0
    return dict(weights)


def profile_v3_factor_values(
    current: CandidateIdentitySpec,
    profile: CandidateIdentityProfileV3,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    current_titles = current.evidence_titles or current.titles
    current_skills = frozenset(
        _normalise_evidence_text(value)
        for value in (current.evidence_skills or current.skills)
        if _normalise_evidence_text(value)
    )
    current_responsibilities = (
        current.evidence_responsibilities or current.responsibilities
    )

    title_recent = _fast_text_similarity(
        current_titles,
        profile.recent_titles,
    )
    title_anchor = _jaccard(
        _title_tokens(current_titles),
        profile.core_title_tokens,
    )
    title_alias_support = _fast_text_similarity(
        current_titles,
        profile.title_alias_history,
    )

    recent_skill_weights = {skill: 1.0 for skill in profile.recent_skills}
    current_skill_weights = {skill: 1.0 for skill in current_skills}
    skill_recent = weighted_jaccard(current_skill_weights, recent_skill_weights)
    skill_anchor = weighted_jaccard(
        current_skill_weights,
        profile.top_skill_anchor_weights(config),
    )

    responsibility_recent = _fast_text_similarity(
        current_responsibilities,
        profile.recent_responsibilities,
    )
    responsibility_anchor = weighted_jaccard(
        _current_bigram_weights(current_responsibilities),
        profile.top_responsibility_anchor_weights(config),
    )
    responsibility_alias_support = (
        _fast_text_similarity(
            tuple(sorted(current_responsibilities))[:50],
            tuple(sorted(profile.responsibility_alias_history))[:200],
        )
        if len(current_responsibilities) <= 50
        else None
    )

    sample = _jaccard(
        current.member_evidence_ids,
        profile.member_evidence_ids,
    )
    dedup = _jaccard(
        current.member_dedup_cluster_ids,
        profile.member_dedup_cluster_ids,
    )
    template = _jaccard(
        current.member_template_cluster_ids,
        profile.member_template_cluster_ids,
    )
    overlaps = [value for value in (sample, dedup, template) if value is not None]
    membership = max(overlaps, default=0.0)
    semantic = _cosine(current.semantic_centroid, profile.semantic_centroid) or None
    components = {
        "title_recent": round(title_recent, 6),
        "title_anchor": round(title_anchor, 6),
        "title_alias_support": round(title_alias_support, 6),
        "skill_recent": round(skill_recent, 6),
        "skill_anchor": round(skill_anchor, 6),
        "responsibility_recent": round(responsibility_recent, 6),
        "responsibility_anchor": round(responsibility_anchor, 6),
        "responsibility_alias_support": (
            round(responsibility_alias_support, 6)
            if responsibility_alias_support is not None
            else None
        ),
        "membership_overlap": round(membership, 6),
        "semantic_similarity": (
            round(semantic, 6) if semantic is not None else None
        ),
        "evidence_confidence": round(profile.anchor_gate(config), 6),
        "recent_strong_signal": round(
            max(
                title_recent,
                responsibility_recent,
                skill_recent,
            ),
            6,
        ),
    }
    return {
        "profile_version": profile.profile_version,
        "support_window_count": profile.support_window_count,
        "recent_window_ids": list(profile.recent_window_ids),
        "components": components,
    }


def profile_v3_compact_summary(
    profile: CandidateIdentityProfileV3,
) -> dict[str, Any]:
    return {
        "profile_version": profile.profile_version,
        "recent_window_ids": list(profile.recent_window_ids),
        "support_window_count": profile.support_window_count,
        "recent_titles": sorted(profile.recent_titles)[:5],
        "recent_skills": sorted(profile.recent_skills)[:10],
        "core_title_tokens": sorted(profile.core_title_tokens)[:10],
        "skill_count": len(profile.skill_window_frequency),
        "responsibility_alias_count": len(profile.responsibility_alias_history),
    }
