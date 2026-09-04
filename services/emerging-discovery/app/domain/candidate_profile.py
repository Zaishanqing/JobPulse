"""Candidate Identity Profile v2 (challenger-only).

Profile v2 replaces the long-running cumulative union profile with three
structured layers:

- recent_profile: the last 1-2 eligible windows, used for continuity;
- identity_anchor: window-frequency weighted skills, stable responsibility
  bigrams, semantic EMA centroid, core title tokens and support counts;
- alias_history: historical title/responsibility aliases, kept only as an
  auxiliary retrieval/support signal and never as a standalone same decision.

The module is deliberately additive.  It does not alter the production
``candidate_identity.py`` linker or any persisted Candidate schema.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from hashlib import sha256
from typing import Any, Mapping, Sequence

from app.domain.candidate_identity import (
    CandidateIdentitySpec,
    _cosine,
    _jaccard,
    _normalise_evidence_text,
    _tokens,
)


PROFILE_V2_VERSION = "candidate-identity-profile.v2"
PROFILE_V2_REBUILD_SCHEMA = "candidate-identity-profile-rebuild.v1"

DEFAULT_PROFILE_V2_CONFIG: dict[str, Any] = {
    "recent_window_count": 2,
    "anchor_support_min_windows": 2,
    "title_recent_weight": 0.20,
    "title_anchor_weight": 0.80,
    "responsibility_recent_weight": 0.10,
    "responsibility_anchor_weight": 0.90,
    "semantic_ema_alpha": 0.5,
    "skill_weight_floor": 0.0,
    "skill_support_min_ratio": 0.5,
    "responsibility_bigram_support_min_ratio": 0.25,
    "alias_history_support_only": True,
}


@dataclass(frozen=True)
class CandidateWindowEvidence:
    """One window's raw evidence used to build a Profile v2 candidate."""

    window_id: str
    titles: frozenset[str] = frozenset()
    skills: frozenset[str] = frozenset()
    responsibilities: frozenset[str] = frozenset()
    semantic_centroid: tuple[float, ...] = ()
    member_evidence_ids: frozenset[str] = frozenset()
    member_dedup_cluster_ids: frozenset[str] = frozenset()
    member_template_cluster_ids: frozenset[str] = frozenset()
    sample_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class CandidateIdentityProfileV2:
    """Deterministic, versioned Candidate profile used by the challenger."""

    profile_version: str = PROFILE_V2_VERSION
    candidate_id: str | None = None
    recent_window_ids: tuple[str, ...] = ()
    recent_window_evidence: tuple[CandidateWindowEvidence, ...] = ()
    recent_titles: frozenset[str] = frozenset()
    recent_skills: frozenset[str] = frozenset()
    recent_responsibilities: frozenset[str] = frozenset()
    recent_semantic_centroid: tuple[float, ...] = ()
    skill_window_frequency: dict[str, int] = field(default_factory=dict)
    responsibility_window_frequency: dict[str, int] = field(default_factory=dict)
    responsibility_bigram_window_frequency: dict[str, int] = field(default_factory=dict)
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
                "skill_support_ratio": self.skill_support_ratio,
                "responsibility_frequency": dict(
                    sorted(self.responsibility_window_frequency.items())
                ),
                "responsibility_bigram_frequency": dict(
                    sorted(self.responsibility_bigram_window_frequency.items())
                ),
                "responsibility_bigram_support_ratio": (
                    self.responsibility_bigram_support_ratio
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
    def from_dict(cls, value: Mapping[str, Any]) -> "CandidateIdentityProfileV2":
        recent = value.get("recent_profile") or {}
        anchor = value.get("identity_anchor") or {}
        aliases = value.get("alias_history") or {}
        return cls(
            profile_version=str(value.get("profile_version", PROFILE_V2_VERSION)),
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


def _profile_v2_config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    merged = {**DEFAULT_PROFILE_V2_CONFIG, **(config or {})}
    recent_count = int(merged["recent_window_count"])
    if recent_count < 1:
        raise ValueError("profile v2 recent_window_count must be positive")
    title_recent = float(merged["title_recent_weight"])
    title_anchor = float(merged["title_anchor_weight"])
    responsibility_recent = float(merged["responsibility_recent_weight"])
    responsibility_anchor = float(merged["responsibility_anchor_weight"])
    if any(
        value < 0
        for value in (
            title_recent,
            title_anchor,
            responsibility_recent,
            responsibility_anchor,
            float(merged["semantic_ema_alpha"]),
            float(merged["skill_weight_floor"]),
            float(merged["skill_support_min_ratio"]),
            float(merged["responsibility_bigram_support_min_ratio"]),
        )
    ):
        raise ValueError("profile v2 weights and floors must be non-negative")
    if abs(title_recent + title_anchor - 1.0) > 1e-9:
        raise ValueError("profile v2 title weights must sum to one")
    if abs(responsibility_recent + responsibility_anchor - 1.0) > 1e-9:
        raise ValueError("profile v2 responsibility weights must sum to one")
    if not 0 <= float(merged["semantic_ema_alpha"]) <= 1:
        raise ValueError("profile v2 semantic EMA alpha must be between zero and one")
    return merged


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def profile_v2_config_version(config: Mapping[str, Any] | None = None) -> str:
    merged = _profile_v2_config(config)
    digest = sha256(_canonical_json(merged).encode("utf-8")).hexdigest()[:16]
    return f"{PROFILE_V2_VERSION}/sha256:{digest}"


def empty_profile_v2(candidate_id: str | None = None) -> CandidateIdentityProfileV2:
    return CandidateIdentityProfileV2(candidate_id=candidate_id)


def _normalise_skills(skills: Sequence[str]) -> frozenset[str]:
    return frozenset(
        _normalise_evidence_text(str(value))
        for value in skills
        if _normalise_evidence_text(str(value))
    )


def window_evidence_from_samples(
    window_id: str,
    sample_ids: Sequence[str],
    samples_by_id: Mapping[str, Mapping[str, Any]],
) -> CandidateWindowEvidence:
    titles: set[str] = set()
    skills: set[str] = set()
    responsibilities: set[str] = set()
    evidence_ids: set[str] = set()
    dedup_ids: set[str] = set()
    template_ids: set[str] = set()
    centroids: list[tuple[float, ...]] = []
    for sample_id in sample_ids:
        row = samples_by_id.get(str(sample_id))
        if row is None:
            continue
        if row.get("title"):
            titles.add(str(row["title"]))
        for skill in row.get("skills") or ():
            if str(skill).strip():
                skills.add(str(skill))
        responsibilities_text = row.get("responsibilities") or row.get(
            "responsibility_excerpts"
        ) or ()
        for text in responsibilities_text:
            if str(text).strip():
                responsibilities.add(str(text))
        if row.get("evidence_identity"):
            evidence_ids.add(str(row["evidence_identity"]))
        if row.get("dedup_cluster_id"):
            dedup_ids.add(str(row["dedup_cluster_id"]))
        if row.get("template_cluster_id"):
            template_ids.add(str(row["template_cluster_id"]))
        if row.get("semantic_centroid"):
            centroids.append(tuple(float(item) for item in row["semantic_centroid"]))
    return CandidateWindowEvidence(
        window_id=window_id,
        titles=frozenset(titles),
        skills=_normalise_skills(skills),
        responsibilities=frozenset(responsibilities),
        semantic_centroid=tuple(centroids[0]) if centroids else (),
        member_evidence_ids=frozenset(evidence_ids),
        member_dedup_cluster_ids=frozenset(dedup_ids),
        member_template_cluster_ids=frozenset(template_ids),
        sample_ids=frozenset(str(item) for item in sample_ids),
    )


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
    profile: CandidateIdentityProfileV2,
    evidence: CandidateWindowEvidence,
    config: Mapping[str, Any] | None = None,
) -> CandidateIdentityProfileV2:
    merged = _profile_v2_config(config)
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
        responsibility_grams.update(_bigrams_of_text(str(text)))

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
    return CandidateIdentityProfileV2(
        profile_version=PROFILE_V2_VERSION,
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


def _bigrams_of_text(value: str) -> frozenset[str]:
    from app.domain.candidate_identity import _bigrams

    return _bigrams(value)


@lru_cache(maxsize=None)
def _bigrams_cached(value: str) -> frozenset[str]:
    return _bigrams_of_text(value)


def _fast_text_similarity(
    left: Sequence[str] | frozenset[str],
    right: Sequence[str] | frozenset[str],
) -> float:
    values = [
        _jaccard(_bigrams_cached(left_value), _bigrams_cached(right_value))
        for left_value in left
        for right_value in right
        if _bigrams_cached(left_value) and _bigrams_cached(right_value)
    ]
    return max(values, default=0.0)


def build_profile_v2(
    candidate_id: str | None,
    windows: Sequence[CandidateWindowEvidence],
    config: Mapping[str, Any] | None = None,
    *,
    window_order: Sequence[str] | None = None,
) -> CandidateIdentityProfileV2:
    ordered = list(windows)
    if window_order is not None:
        order = {window_id: index for index, window_id in enumerate(window_order)}
        ordered.sort(
            key=lambda item: (
                order.get(item.window_id, len(order)),
                item.window_id,
            )
        )
    profile = empty_profile_v2(candidate_id)
    for evidence in ordered:
        profile = append_window_evidence(profile, evidence, config)
    return profile


def rebuild_profile_v2_from_observations(
    candidate_id: str,
    observations: Sequence[tuple[str, Sequence[str]] | CandidateWindowEvidence],
    samples_by_id: Mapping[str, Mapping[str, Any]],
    config: Mapping[str, Any] | None = None,
    *,
    window_order: Sequence[str] | None = None,
) -> CandidateIdentityProfileV2:
    """Deterministic rebuild from per-window observations for migration/backfill."""

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
    return build_profile_v2(
        candidate_id,
        windows,
        config,
        window_order=window_order,
    )


def rebuild_profile_v2_from_legacy_record(
    legacy_record: Mapping[str, Any],
    observations: Sequence[tuple[str, Sequence[str]] | CandidateWindowEvidence],
    samples_by_id: Mapping[str, Mapping[str, Any]],
    config: Mapping[str, Any] | None = None,
    *,
    window_order: Sequence[str] | None = None,
) -> CandidateIdentityProfileV2:
    """Compatibility rebuild from an old Candidate record plus observation rows."""

    return rebuild_profile_v2_from_observations(
        str(legacy_record.get("id") or legacy_record.get("candidate_id") or "legacy"),
        observations,
        samples_by_id,
        config,
        window_order=window_order,
    )


def weighted_jaccard(
    left: Mapping[str, float],
    right: Mapping[str, float],
) -> float:
    keys = set(left) | set(right)
    if not keys:
        return 0.0
    intersection = sum(
        min(left.get(key, 0.0), right.get(key, 0.0)) for key in keys
    )
    union = sum(max(left.get(key, 0.0), right.get(key, 0.0)) for key in keys)
    return intersection / union if union else 0.0


def _title_tokens(titles: Sequence[str] | frozenset[str]) -> frozenset[str]:
    tokens: set[str] = set()
    for title in titles:
        tokens.update(_tokens(str(title)))
    return frozenset(tokens)


def _current_bigram_weights(
    responsibilities: Sequence[str] | frozenset[str],
) -> dict[str, float]:
    weights: Counter[str] = Counter()
    for text in responsibilities:
        for gram in _bigrams_cached(str(text)):
            weights[gram] = 1.0
    return dict(weights)


def profile_v2_factor_values(
    current: CandidateIdentitySpec,
    profile: CandidateIdentityProfileV2,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute verifier-ready factors from a Profile v2 candidate."""

    merged = _profile_v2_config(config)
    current_titles = current.evidence_titles or current.titles
    current_skills = _normalise_skills(current.evidence_skills or current.skills)
    current_responsibilities = (
        current.evidence_responsibilities or current.responsibilities
    )

    recent_title = _fast_text_similarity(current_titles, profile.recent_titles)
    anchor_title = _jaccard(
        _title_tokens(current_titles),
        profile.core_title_tokens,
    )
    title = (
        float(merged["title_recent_weight"]) * recent_title
        + float(merged["title_anchor_weight"]) * anchor_title
    )
    alias_title_support = _fast_text_similarity(
        current_titles,
        profile.title_alias_history,
    )

    floor = float(merged["skill_weight_floor"])
    skill_min_ratio = float(merged["skill_support_min_ratio"])
    profile_skill_weights = {
        skill: max(ratio, floor)
        for skill, ratio in profile.skill_support_ratio.items()
        if ratio >= skill_min_ratio
    }
    current_skill_weights = {skill: 1.0 for skill in current_skills}
    skill = weighted_jaccard(current_skill_weights, profile_skill_weights)

    recent_responsibility = _fast_text_similarity(
        current_responsibilities,
        profile.recent_responsibilities,
    )
    responsibility_min_ratio = float(
        merged["responsibility_bigram_support_min_ratio"]
    )
    profile_responsibility_weights = {
        gram: ratio
        for gram, ratio in profile.responsibility_bigram_support_ratio.items()
        if ratio >= responsibility_min_ratio
    }
    anchor_responsibility = weighted_jaccard(
        _current_bigram_weights(current_responsibilities),
        profile_responsibility_weights,
    )
    responsibility = (
        float(merged["responsibility_recent_weight"]) * recent_responsibility
        + float(merged["responsibility_anchor_weight"]) * anchor_responsibility
    )
    alias_responsibility_support = (
        _fast_text_similarity(
            tuple(sorted(current_responsibilities))[:50],
            tuple(sorted(profile.responsibility_alias_history))[:200],
        )
        if len(current_responsibilities) <= 50
        else None
    )

    sample = _jaccard(current.member_evidence_ids, profile.member_evidence_ids)
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
    return {
        "title": round(title, 6),
        "skill": round(skill, 6),
        "responsibility": round(responsibility, 6),
        "membership": round(membership, 6),
        "semantic": round(semantic, 6) if semantic is not None else None,
        "profile_version": profile.profile_version,
        "support_window_count": profile.support_window_count,
        "recent_window_ids": list(profile.recent_window_ids),
        "components": {
            "title_recent": round(recent_title, 6),
            "title_anchor": round(anchor_title, 6),
            "title_alias_support": round(alias_title_support, 6),
            "skill_weighted_jaccard": round(skill, 6),
            "responsibility_recent": round(recent_responsibility, 6),
            "responsibility_anchor": round(anchor_responsibility, 6),
            "responsibility_alias_support": (
                round(alias_responsibility_support, 6)
                if alias_responsibility_support is not None
                else None
            ),
            "membership_overlap": round(membership, 6),
            "semantic_similarity": (
                round(semantic, 6) if semantic is not None else None
            ),
        },
    }


def profile_v2_compact_summary(
    profile: CandidateIdentityProfileV2,
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


def consecutive_continuity_increment(
    *,
    previous_identity_stability: int = 0,
    decision: str,
    previous_window_id: str | None,
    current_window_id: str | None,
    window_order: Sequence[str] | None = None,
    eligible: bool = True,
) -> int:
    """Return the identity-stability counter under the Profile v2 contract.

    A first observation, a missing window, a non-same decision, or a
    non-adjacent previous window always resets to zero.  Only an automatic
    ``same`` decision with an adjacent eligible previous window increments.
    """

    if not eligible or decision != "same":
        return 0
    if previous_window_id is None or current_window_id is None:
        return 0
    if window_order is not None:
        if (
            previous_window_id not in window_order
            or current_window_id not in window_order
        ):
            return 0
        previous_index = window_order.index(previous_window_id)
        current_index = window_order.index(current_window_id)
        if abs(current_index - previous_index) != 1:
            return 0
    return previous_identity_stability + 1
