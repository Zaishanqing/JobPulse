"""Deterministic candidate identity matching across discovery runs."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Any
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256

from app.domain.values import JsonObject, freeze, thaw


DEFAULT_CANDIDATE_IDENTITY_CONFIG: dict[str, float] = {
    "title_similarity_weight": 0.20,
    "skill_similarity_weight": 0.35,
    "responsibility_similarity_weight": 0.25,
    "membership_overlap_weight": 0.10,
    "semantic_similarity_weight": 0.10,
    "identity_match_threshold": 0.55,
}
LEGACY_DECISION_VERSION = "candidate-identity-v1"
SELECTED_DECISION_VERSION = "conservative-reviewed-evidence-linker.v1"
SELECTED_CONFIG_VERSION = (
    "conservative-reviewed-evidence-linker.v1/"
    "sha256:4a078a1164694e5842f6c1bd82e86fa45eb352b292be4083341fe674cd595d9c"
)
DEFAULT_SELECTED_LINKER_CONFIG: dict[str, float] = {
    "title_threshold": 0.15,
    "responsibility_threshold": 0.042334,
    "skills_threshold": 0.5,
}
IDENTITY_V2_DECISION_VERSION = "identity-v2-semantic-temporal-verifier.v1"
DEFAULT_IDENTITY_V2_CONFIG: dict[str, float] = {
    # Preregistered architecture weights (not tuned): title is the strongest
    # identity signal in the frozen corpus; the contradiction penalty is a
    # strong veto over title-only merges.
    "semantic_similarity_weight": 0.20,
    "responsibility_similarity_weight": 0.15,
    "skill_similarity_weight": 0.10,
    "title_similarity_weight": 0.45,
    "temporal_prior_weight": 0.10,
    "contradiction_penalty_weight": 0.35,
    "verifier_accept_threshold": 0.60,
    "verifier_review_margin": 0.05,
    "verifier_top2_margin": 0.05,
    "contradiction_title_threshold": 0.30,
    "contradiction_responsibility_threshold": 0.05,
    "contradiction_penalty_value": 1.0,
    "temporal_prior_adjacent": 1.0,
    "temporal_prior_non_adjacent": 0.0,
    "semantic_unavailable_policy": "renormalize",
    "stage1_d14_linker_version": SELECTED_DECISION_VERSION,
}
IDENTITY_V2_CONFIG_BASE_VERSION = "identity-v2-semantic-temporal-verifier.v1"
SUPPORTED_LINKER_VERSIONS = frozenset(
    {LEGACY_DECISION_VERSION, SELECTED_DECISION_VERSION, IDENTITY_V2_DECISION_VERSION}
)
ABSTENTION_POLICY_VERSION = "candidate-identity-abstention.v1"
IDENTITY_V2_ABSTENTION_POLICY_VERSION = "candidate-identity-abstention.v1"
DEFAULT_ABSTENTION_CONFIG: dict[str, float] = {
    "identity_abstention_margin": 0.05,
    "identity_abstention_conflict_gap": 0.25,
}
PENDING_IDENTITY_REVIEW = "pending_review"
RESOLVED_SAME = "resolved_same"
RESOLVED_NEW = "resolved_new"
IDENTITY_RESOLUTION_STATES = frozenset(
    {PENDING_IDENTITY_REVIEW, RESOLVED_SAME, RESOLVED_NEW}
)
PENDING_IDENTITY_RESOLUTION_SCHEMA_VERSION = "provisional-identity-resolution.v1"


@dataclass(frozen=True)
class CandidateIdentitySpec:
    titles: frozenset[str]
    skills: frozenset[str]
    responsibilities: frozenset[str]
    member_jd_ids: frozenset[str] = frozenset()
    semantic_centroid: tuple[float, ...] = ()
    candidate_id: str | None = None
    evidence_titles: frozenset[str] = frozenset()
    evidence_skills: frozenset[str] = frozenset()
    evidence_responsibilities: frozenset[str] = frozenset()
    member_evidence_ids: frozenset[str] = frozenset()
    member_dedup_cluster_ids: frozenset[str] = frozenset()
    member_template_cluster_ids: frozenset[str] = frozenset()
    last_seen_window_id: str | None = None


@dataclass(frozen=True)
class CandidateIdentityComponents:
    title_similarity: float | None
    skill_similarity: float | None
    responsibility_similarity: float | None
    membership_overlap: float | None
    semantic_similarity: float | None
    sample_overlap: float | None = None
    dedup_cluster_overlap: float | None = None
    template_cluster_overlap: float | None = None


@dataclass(frozen=True)
class CandidateIdentityMatch:
    candidate_id: str | None
    identity_similarity: float
    components: CandidateIdentityComponents
    threshold: float
    matched: bool
    decision_reason: str
    decision_version: str = LEGACY_DECISION_VERSION
    config_version: str = "candidate-identity-v1/default-config-v1"
    semantic_status: str = "unavailable"
    decision_basis: tuple[str, ...] = ()
    margin: float | None = None
    abstain: bool = False
    abstention_reason: str | None = None
    verifier: "IdentityV2VerifierComponents | None" = None


@dataclass(frozen=True)
class IdentityV2VerifierComponents:
    semantic_score: float | None
    responsibility_score: float
    skill_score: float
    title_score: float
    temporal_prior: float
    contradiction_penalty: float
    final_score: float
    semantic_status: str
    weights: tuple[float, ...]
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "semantic_score": self.semantic_score,
            "responsibility_score": self.responsibility_score,
            "skill_score": self.skill_score,
            "title_score": self.title_score,
            "temporal_prior": self.temporal_prior,
            "contradiction_penalty": self.contradiction_penalty,
            "final_score": self.final_score,
            "semantic_status": self.semantic_status,
            "weights": list(self.weights),
            "explanation": self.explanation,
        }


@dataclass(frozen=True)
class PendingIdentityResolution:
    """Formal, persisted review context for an ambiguous identity decision."""

    provisional_candidate_id: str
    closest_candidate_id: str | None
    identity_score: float | None
    decision_margin: float | None
    decision_basis: tuple[str, ...]
    continuity_certificate: JsonObject
    window_id: str
    cluster_id: str
    created_at: datetime
    observation_id: str | None = None
    run_id: str | None = None
    algorithm_version: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": PENDING_IDENTITY_RESOLUTION_SCHEMA_VERSION,
            "provisional_candidate_id": self.provisional_candidate_id,
            "closest_candidate_id": self.closest_candidate_id,
            "identity_score": self.identity_score,
            "decision_margin": self.decision_margin,
            "decision_basis": self.decision_basis,
            "continuity_certificate": thaw(self.continuity_certificate),
            "window_id": self.window_id,
            "cluster_id": self.cluster_id,
            "created_at": self.created_at.isoformat(),
            "observation_id": self.observation_id,
            "run_id": self.run_id,
            "algorithm_version": self.algorithm_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "PendingIdentityResolution":
        certificate = value.get("continuity_certificate") or {}
        return cls(
            provisional_candidate_id=str(value["provisional_candidate_id"]),
            closest_candidate_id=(
                str(value["closest_candidate_id"])
                if value.get("closest_candidate_id") is not None
                else None
            ),
            identity_score=(
                float(value["identity_score"])
                if value.get("identity_score") is not None
                else None
            ),
            decision_margin=(
                float(value["decision_margin"])
                if value.get("decision_margin") is not None
                else None
            ),
            decision_basis=tuple(
                str(item) for item in value.get("decision_basis", ())
            ),
            continuity_certificate=freeze(certificate),
            window_id=str(value["window_id"]),
            cluster_id=str(value["cluster_id"]),
            created_at=datetime.fromisoformat(str(value["created_at"])),
            observation_id=(
                str(value["observation_id"])
                if value.get("observation_id") is not None
                else None
            ),
            run_id=(
                str(value["run_id"]) if value.get("run_id") is not None else None
            ),
            algorithm_version=(
                str(value["algorithm_version"])
                if value.get("algorithm_version") is not None
                else None
            ),
        )


def identity_decision(match: CandidateIdentityMatch) -> str:
    """Return the calibrated decision: same, review_required, or new."""
    if match.abstain:
        return "review_required"
    return "same" if match.matched else "new"


def _tokens(value: str) -> frozenset[str]:
    text = value.casefold()
    words = re.findall(r"[a-z0-9_+#.-]+|[\u4e00-\u9fff]{2,}", text)
    # 中文按原始文本中的独立 CJK run/segment 分别生成 bigram，
    # 严禁跨标点/空白/分隔符拼接（例如“机器学习 / 大模型”不得生成“习大”）。
    for segment in re.findall(r"[\u4e00-\u9fff]+", text):
        words.extend(
            segment[index : index + 2]
            for index in range(max(0, len(segment) - 1))
        )
    return frozenset(words)


def _title_set(titles: frozenset[str]) -> frozenset[str]:
    tokens: set[str] = set()
    for title in titles:
        tokens.update(_tokens(title))
    return frozenset(tokens)


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _optional_jaccard(left: frozenset[str], right: frozenset[str]) -> float | None:
    union = left | right
    return len(left & right) / len(union) if union else None


def _normalise_evidence_text(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value.casefold())


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _bigrams(value: str) -> frozenset[str]:
    """Generate bigrams while keeping previous ASCII behaviour and fixing CJK boundaries.

    ASCII runs are concatenated exactly as the historical normalisation did, so
    pure-ASCII similarity remains compatible. CJK runs are bigrammed per original
    segment only, so “机器学习 / 大模型” never yields the fake “习大” token.
    """
    text = value.casefold()
    result: set[str] = set()
    ascii_runs = re.findall(r"[0-9a-z]+", text)
    ascii_text = "".join(ascii_runs)
    if ascii_text:
        if len(ascii_text) == 1:
            result.add(ascii_text)
        else:
            result.update(
                ascii_text[index : index + 2]
                for index in range(len(ascii_text) - 1)
            )
    for segment in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(segment) == 1:
            result.add(segment)
        else:
            result.update(
                segment[index : index + 2]
                for index in range(len(segment) - 1)
            )
    return frozenset(result)


def _text_similarity(left: frozenset[str], right: frozenset[str]) -> float:
    pairs = [
        _jaccard(_bigrams(left_value), _bigrams(right_value))
        for left_value in left
        for right_value in right
        if _bigrams(left_value) and _bigrams(right_value)
    ]
    return max(pairs, default=0.0)


def evidence_identity(source_fact_id: str, source_fact_version: str) -> str:
    """Return the persisted, versioned identity of an input evidence item."""
    return f"{source_fact_id}:{source_fact_version}"


def dedup_cluster_identity(source_fact_version: str) -> str:
    """Reproduce D14's source-independent exact-content cluster identity."""
    digest = sha256(source_fact_version.encode("utf-8")).hexdigest()[:16]
    return f"dedup-{digest}"


def template_cluster_identity(
    company_name: str, title: str, responsibilities: tuple[str, ...]
) -> str:
    """Reproduce D14's traceable company/title/responsibility template identity."""
    basis = json.dumps(
        {
            "company_name": _normalise_evidence_text(company_name),
            "title": _normalise_evidence_text(title),
            "responsibilities": [
                _normalise_evidence_text(value) for value in responsibilities
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"template-{sha256(basis.encode('utf-8')).hexdigest()[:16]}"


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    if denominator == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / denominator


def _merge_config(config: Mapping[str, float] | None) -> dict[str, float]:
    merged = {**DEFAULT_CANDIDATE_IDENTITY_CONFIG, **(config or {})}
    weights = (
        float(merged["title_similarity_weight"]),
        float(merged["skill_similarity_weight"]),
        float(merged["responsibility_similarity_weight"]),
        float(merged["membership_overlap_weight"]),
        float(merged["semantic_similarity_weight"]),
    )
    if any(value < 0 for value in weights):
        raise ValueError("candidate identity weights must be non-negative")
    if abs(sum(weights) - 1.0) > 1e-9:
        raise ValueError("candidate identity weights must sum to one")
    threshold = float(merged["identity_match_threshold"])
    if not 0 <= threshold <= 1:
        raise ValueError("identity match threshold must be between zero and one")
    return merged


def _legacy_candidate_hypotheses(
    current: CandidateIdentitySpec,
    candidates: tuple[CandidateIdentitySpec, ...],
    config: Mapping[str, float] | None = None,
    *,
    top_k: int = 1,
) -> list[CandidateIdentityMatch]:
    merged = _merge_config(config)
    threshold = float(merged["identity_match_threshold"])
    frozen_d5_replay = (
        str((config or {}).get("experiment_policy", ""))
        == "d5-short-window-frozen-no-threshold-tuning.v1"
    )
    abstention_disabled = (
        frozen_d5_replay
        or str((config or {}).get("identity_abstention_mode", "enabled")).casefold()
        == "disabled"
    )
    abstention_margin = float(
        0.0
        if frozen_d5_replay
        else (config or {}).get(
            "identity_abstention_margin",
            DEFAULT_ABSTENTION_CONFIG["identity_abstention_margin"],
        )
    )
    config_version = str(
        (config or {}).get(
            "candidate_identity_config_version",
            "candidate-identity-v1/default-config-v1",
        )
    )
    current_title_set = _title_set(current.titles)

    if not candidates:
        return [
            CandidateIdentityMatch(
            candidate_id=None,
            identity_similarity=1.0,
            components=CandidateIdentityComponents(1.0, 1.0, 1.0, 1.0, None),
            threshold=threshold,
            matched=False,
            decision_reason="first observation creates candidate; no historical candidate matched",
            config_version=config_version,
            semantic_status="unavailable",
            decision_basis=("no_historical_candidate",),
            margin=None,
            abstain=False,
            abstention_reason=None,
            )
        ]

    scored: list[tuple[float, CandidateIdentityComponents, CandidateIdentitySpec, str]] = []
    for candidate in sorted(candidates, key=lambda item: item.candidate_id or ""):
        title = _jaccard(current_title_set, _title_set(candidate.titles))
        skill = _jaccard(current.skills, candidate.skills)
        responsibility = _jaccard(current.responsibilities, candidate.responsibilities)
        membership = _jaccard(current.member_jd_ids, candidate.member_jd_ids)
        semantic = _cosine(current.semantic_centroid, candidate.semantic_centroid) or None
        components = CandidateIdentityComponents(
            round(title, 6),
            round(skill, 6),
            round(responsibility, 6),
            round(membership, 6),
            round(semantic, 6) if semantic is not None else None,
        )
        if semantic is not None:
            available_weights = (
                float(merged["title_similarity_weight"]),
                float(merged["skill_similarity_weight"]),
                float(merged["responsibility_similarity_weight"]),
                float(merged["membership_overlap_weight"]),
                float(merged["semantic_similarity_weight"]),
            )
            values = (title, skill, responsibility, membership, semantic)
            semantic_note = "semantic cosine"
        else:
            names = (
                "title_similarity_weight",
                "skill_similarity_weight",
                "responsibility_similarity_weight",
                "membership_overlap_weight",
            )
            available_weights = tuple(float(merged[name]) for name in names)
            values = (title, skill, responsibility, membership)
            semantic_note = "semantic unavailable, weights renormalized"
        total_weight = sum(available_weights)
        identity = (
            sum(value * weight for value, weight in zip(values, available_weights, strict=True))
            / total_weight
            if total_weight > 0
            else 0.0
        )
        reason = (
            f"title {round(title, 6)}; skills {round(skill, 6)}; "
            f"responsibilities {round(responsibility, 6)}; "
            f"membership overlap {round(membership, 6)}; {semantic_note}"
        )
        scored.append((round(identity, 6), components, candidate, reason))

    scored.sort(
        key=lambda item: (item[0], item[2].candidate_id or ""),
        reverse=True,
    )
    matches: list[CandidateIdentityMatch] = []
    for identity, components, best, reason in scored[:top_k]:
        matched = identity >= threshold
        if matched:
            decision = (
                f"identity_similarity {identity} >= threshold {threshold}; " + reason
            )
        else:
            decision = (
                f"identity_similarity {identity} < threshold {threshold}; "
                f"closest candidate {best.candidate_id}; " + reason
            )
        margin = round(identity - threshold, 6)
        abstain = abs(margin) <= abstention_margin
        if abstention_disabled:
            abstain = False
        matches.append(
            CandidateIdentityMatch(
                candidate_id=best.candidate_id,
                identity_similarity=identity,
                components=components,
                threshold=threshold,
                matched=matched,
                decision_reason=decision,
                config_version=config_version,
                semantic_status=(
                    "available_used"
                    if components.semantic_similarity is not None
                    else "unavailable"
                ),
                decision_basis=("weighted_similarity_threshold",),
                margin=margin,
                abstain=abstain,
                abstention_reason=(
                    (
                        f"identity similarity {identity} is within {abstention_margin} "
                        f"of decision threshold {threshold}"
                    )
                    if abstain
                    else None
                ),
            )
        )
    return matches


def match_candidate_identity(
    current: CandidateIdentitySpec,
    candidates: tuple[CandidateIdentitySpec, ...],
    config: Mapping[str, float] | None = None,
) -> CandidateIdentityMatch:
    return _legacy_candidate_hypotheses(current, candidates, config, top_k=1)[0]


def _selected_config(config: Mapping[str, object] | None) -> dict[str, float]:
    configured_version = str(
        (config or {}).get("candidate_identity_config_version", SELECTED_CONFIG_VERSION)
    )
    if configured_version != SELECTED_CONFIG_VERSION:
        raise ValueError(
            "selected candidate identity linker requires its locked D14 config version"
        )
    merged = {
        **DEFAULT_SELECTED_LINKER_CONFIG,
        **{
            key: float((config or {})[key])
            for key in DEFAULT_SELECTED_LINKER_CONFIG
            if key in (config or {})
        },
    }
    # The selected production method is the already-locked D14 method. Runtime
    # threshold overrides would silently turn it into an unreviewed algorithm.
    if merged != DEFAULT_SELECTED_LINKER_CONFIG:
        raise ValueError("selected candidate identity thresholds are locked by D14")
    return merged


def selected_identity_basis(
    *,
    title_similarity: float,
    skill_similarity: float,
    responsibility_similarity: float,
    membership_overlap: float,
    config: Mapping[str, object] | None = None,
) -> tuple[str, ...]:
    """Return the locked D14 OR-rule reasons for already computed factors."""
    locked = _selected_config(config)
    reasons: list[str] = []
    if membership_overlap > 0.0:
        reasons.append("membership_overlap_positive")
    if title_similarity >= locked["title_threshold"]:
        reasons.append("title_reaches_train_midpoint")
    if (
        skill_similarity >= locked["skills_threshold"]
        and responsibility_similarity >= locked["responsibility_threshold"]
    ):
        reasons.append("majority_skills_and_responsibility_midpoint")
    return tuple(reasons)


def _selected_candidate_hypotheses(
    current: CandidateIdentitySpec,
    candidates: tuple[CandidateIdentitySpec, ...],
    config: Mapping[str, object] | None = None,
    *,
    top_k: int = 1,
) -> list[CandidateIdentityMatch]:
    """Apply the selected D14 evidence rule to production Candidate specs."""
    locked = _selected_config(config)
    frozen_d5_replay = (
        str((config or {}).get("experiment_policy", ""))
        == "d5-short-window-frozen-no-threshold-tuning.v1"
    )
    abstention_disabled = (
        frozen_d5_replay
        or str((config or {}).get("identity_abstention_mode", "enabled")).casefold()
        == "disabled"
    )
    abstention_margin = float(
        0.0
        if frozen_d5_replay
        else (config or {}).get(
            "identity_abstention_margin",
            DEFAULT_ABSTENTION_CONFIG["identity_abstention_margin"],
        )
    )
    abstention_conflict_gap = float(
        1.0
        if frozen_d5_replay
        else (config or {}).get(
            "identity_abstention_conflict_gap",
            DEFAULT_ABSTENTION_CONFIG["identity_abstention_conflict_gap"],
        )
    )
    semantic_status = "available_not_used" if current.semantic_centroid else "unavailable"
    if not candidates:
        return [
            CandidateIdentityMatch(
            candidate_id=None,
            identity_similarity=1.0,
            components=CandidateIdentityComponents(None, None, None, None, None),
            threshold=1.0,
            matched=False,
            decision_reason="first observation creates candidate; no historical candidate matched",
            decision_version=SELECTED_DECISION_VERSION,
            config_version=SELECTED_CONFIG_VERSION,
            semantic_status=semantic_status,
            decision_basis=("no_historical_candidate",),
            margin=None,
            abstain=False,
            abstention_reason=None,
            )
        ]

    current_titles = current.evidence_titles or current.titles
    current_skills = frozenset(
        _normalise_evidence_text(value)
        for value in (current.evidence_skills or current.skills)
        if _normalise_evidence_text(value)
    )
    current_responsibilities = (
        current.evidence_responsibilities or current.responsibilities
    )
    scored: list[
        tuple[
            tuple[float, ...],
            CandidateIdentityComponents,
            CandidateIdentitySpec,
            tuple[str, ...],
        ]
    ] = []
    for candidate in sorted(candidates, key=lambda item: item.candidate_id or ""):
        candidate_titles = candidate.evidence_titles or candidate.titles
        candidate_skills = frozenset(
            _normalise_evidence_text(value)
            for value in (candidate.evidence_skills or candidate.skills)
            if _normalise_evidence_text(value)
        )
        candidate_responsibilities = (
            candidate.evidence_responsibilities or candidate.responsibilities
        )
        title = _text_similarity(current_titles, candidate_titles)
        skill = _jaccard(current_skills, candidate_skills)
        responsibility = _text_similarity(
            current_responsibilities, candidate_responsibilities
        )
        sample = _optional_jaccard(
            current.member_evidence_ids, candidate.member_evidence_ids
        )
        dedup = _optional_jaccard(
            current.member_dedup_cluster_ids, candidate.member_dedup_cluster_ids
        )
        template = _optional_jaccard(
            current.member_template_cluster_ids, candidate.member_template_cluster_ids
        )
        available_overlaps = [
            value for value in (sample, dedup, template) if value is not None
        ]
        membership = max(available_overlaps, default=0.0)
        reasons = selected_identity_basis(
            title_similarity=title,
            skill_similarity=skill,
            responsibility_similarity=responsibility,
            membership_overlap=membership,
            config=config,
        )
        components = CandidateIdentityComponents(
            round(title, 6),
            round(skill, 6),
            round(responsibility, 6),
            round(membership, 6),
            None,
            round(sample, 6) if sample is not None else None,
            round(dedup, 6) if dedup is not None else None,
            round(template, 6) if template is not None else None,
        )
        # D14 defines a boolean OR rule, not a tunable aggregate score. Preserve
        # that contract and use component values only for deterministic tie-breaks.
        rank = (
            float(bool(reasons)),
            float(membership > 0.0),
            float(title >= locked["title_threshold"]),
            float(
                skill >= locked["skills_threshold"]
                and responsibility >= locked["responsibility_threshold"]
            ),
            membership,
            title,
            responsibility,
            skill,
        )
        scored.append((rank, components, candidate, reasons))

    scored.sort(
        key=lambda item: (item[0], item[2].candidate_id or ""),
        reverse=True,
    )
    matches: list[CandidateIdentityMatch] = []
    for _rank, components, best, reasons in scored[:top_k]:
        semantic_status = (
            "available_not_used"
            if current.semantic_centroid and best.semantic_centroid
            else "unavailable"
        )
        matched = bool(reasons)
        semantic_note = (
            "semantic available but not used by linker"
            if semantic_status == "available_not_used"
            else "semantic unavailable; not imputed"
        )
        title_margin = (
            (components.title_similarity or 0.0) - locked["title_threshold"]
        )
        skills_responsibility_margin = min(
            (components.skill_similarity or 0.0) - locked["skills_threshold"],
            (components.responsibility_similarity or 0.0)
            - locked["responsibility_threshold"],
        )
        membership_margin = components.membership_overlap or 0.0
        rule_margins = (
            title_margin,
            skills_responsibility_margin,
        )
        if membership_margin > 0.0:
            rule_margins = (*rule_margins, membership_margin)
        if matched:
            margin = round(min(value for value in rule_margins if value >= 0), 6)
            conflicting = (
                len(reasons) == 1
                and any(
                    value < 0 and value <= -abstention_conflict_gap
                    for value in rule_margins
                )
            )
            abstain = margin <= abstention_margin or conflicting
            if abstain:
                if conflicting:
                    abstention_reason = (
                        "single D14 rule matched while another identity component "
                        f"is at least {abstention_conflict_gap} below its threshold"
                    )
                else:
                    abstention_reason = (
                        f"closest matched rule margin {margin} is within "
                        f"{abstention_margin} of the decision boundary"
                    )
            else:
                abstention_reason = None
        else:
            margin = round(max(rule_margins), 6)
            abstain = -margin <= abstention_margin
            abstention_reason = (
                f"closest rule margin {margin} is within {abstention_margin} "
                "below the decision boundary"
                if abstain
                else None
            )
        if abstention_disabled:
            abstain = False
            abstention_reason = None
        rule_values = (
            f"title {components.title_similarity} (threshold {locked['title_threshold']}); "
            f"skills {components.skill_similarity} (threshold {locked['skills_threshold']}); "
            f"responsibilities {components.responsibility_similarity} "
            f"(threshold {locked['responsibility_threshold']}); membership overlap "
            f"{components.membership_overlap}; {semantic_note}"
        )
        reason = (
            f"same identity by {', '.join(reasons)}; {rule_values}"
            if matched
            else (
                f"no D14 identity rule matched; closest candidate {best.candidate_id}; "
                f"{rule_values}"
            )
        )
        matches.append(
            CandidateIdentityMatch(
                candidate_id=best.candidate_id,
                identity_similarity=1.0 if matched else 0.0,
                components=components,
                threshold=1.0,
                matched=matched,
                decision_reason=reason,
                decision_version=SELECTED_DECISION_VERSION,
                config_version=SELECTED_CONFIG_VERSION,
                semantic_status=semantic_status,
                decision_basis=reasons or ("no_rule_matched",),
                margin=margin,
                abstain=abstain,
                abstention_reason=abstention_reason,
            )
        )
    return matches


def match_selected_candidate_identity(
    current: CandidateIdentitySpec,
    candidates: tuple[CandidateIdentitySpec, ...],
    config: Mapping[str, object] | None = None,
) -> CandidateIdentityMatch:
    return _selected_candidate_hypotheses(current, candidates, config, top_k=1)[0]



# ---------------------------------------------------------------------------
# Identity v2: two-stage semantic + temporal verifier
#
# Stage 1 reuses the D14 selected linker candidate generation (the locked
# conservative-reviewed-evidence-linker.v1 OR rule).  Stage 2 runs a
# configurable, versioned verifier that combines semantic/responsibility/skill/
# title similarity with an adjacent-window temporal prior and a responsibility
# contradiction penalty, and reuses the D16 abstention contract
# (review_required / closest_candidate_id / margin / continuity certificate).
# ---------------------------------------------------------------------------


def _identity_v2_config(
    config: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    configured = (config or {})
    merged: dict[str, Any] = {**DEFAULT_IDENTITY_V2_CONFIG}
    for key in DEFAULT_IDENTITY_V2_CONFIG:
        if key not in configured:
            continue
        if key == "semantic_unavailable_policy":
            merged[key] = str(configured[key])
        elif key == "stage1_d14_linker_version":
            merged[key] = str(configured[key])
        else:
            merged[key] = float(configured[key])
    positive_weights = (
        float(merged["semantic_similarity_weight"]),
        float(merged["responsibility_similarity_weight"]),
        float(merged["skill_similarity_weight"]),
        float(merged["title_similarity_weight"]),
        float(merged["temporal_prior_weight"]),
    )
    positive_weights = (
        float(merged["semantic_similarity_weight"]),
        float(merged["responsibility_similarity_weight"]),
        float(merged["skill_similarity_weight"]),
        float(merged["title_similarity_weight"]),
        float(merged["temporal_prior_weight"]),
    )
    if any(value < 0 for value in positive_weights):
        raise ValueError("identity v2 positive weights must be non-negative")
    if abs(sum(positive_weights) - 1.0) > 1e-9:
        raise ValueError("identity v2 positive weights must sum to one")
    if float(merged["contradiction_penalty_weight"]) < 0:
        raise ValueError("identity v2 contradiction penalty weight must be non-negative")
    threshold = float(merged["verifier_accept_threshold"])
    if not 0 <= threshold <= 1:
        raise ValueError("identity v2 accept threshold must be between zero and one")
    policy = str(merged["semantic_unavailable_policy"])
    if policy not in {"renormalize", "zero"}:
        raise ValueError("identity v2 semantic_unavailable_policy must be renormalize or zero")
    if str(merged["stage1_d14_linker_version"]) != SELECTED_DECISION_VERSION:
        raise ValueError("identity v2 stage 1 requires the locked D14 selected linker")
    return merged


def identity_v2_config_version(
    config: Mapping[str, object] | None = None,
) -> str:
    """Return the versioned config identity for a frozen Identity v2 config."""
    merged = _identity_v2_config(config)
    digest = sha256(_canonical_json(merged).encode("utf-8")).hexdigest()[:16]
    return f"{IDENTITY_V2_CONFIG_BASE_VERSION}/sha256:{digest}"


def _contradiction_penalty(
    *,
    title_similarity: float,
    responsibility_similarity: float,
    config: Mapping[str, object] | None = None,
) -> float:
    """Responsibility contradiction signal: title looks alike but the core
    responsibility evidence disagrees.  This is the only subtractive term."""
    merged = _identity_v2_config(config)
    if (
        title_similarity >= float(merged["contradiction_title_threshold"])
        and responsibility_similarity
        <= float(merged["contradiction_responsibility_threshold"])
    ):
        return float(merged["contradiction_penalty_value"])
    return 0.0


def _temporal_prior(
    *,
    last_seen_window_id: str | None,
    current_window_id: str | None,
    window_order: tuple[str, ...] | None = None,
    config: Mapping[str, object] | None = None,
) -> float:
    """Adjacent-window prior: a candidate observed in the immediately preceding
    window is more likely to continue than a long-gap, incidentally similar one.
    It never overrides an explicit contradiction penalty."""
    merged = _identity_v2_config(config)
    if last_seen_window_id is None or current_window_id is None:
        return float(merged["temporal_prior_non_adjacent"])
    if last_seen_window_id == current_window_id:
        return float(merged["temporal_prior_non_adjacent"])
    if window_order is not None and current_window_id in window_order:
        current_index = window_order.index(current_window_id)
        if current_index > 0 and window_order[current_index - 1] == last_seen_window_id:
            return float(merged["temporal_prior_adjacent"])
        return float(merged["temporal_prior_non_adjacent"])
    # Without an explicit window order, adjacent window ids are opaque; only an
    # exact previous-window pointer is trusted when supplied by the pipeline.
    return float(merged["temporal_prior_non_adjacent"])


def compute_identity_v2_verifier(
    *,
    semantic_similarity: float | None,
    responsibility_similarity: float,
    skill_similarity: float,
    title_similarity: float,
    temporal_prior: float,
    config: Mapping[str, object] | None = None,
) -> IdentityV2VerifierComponents:
    """Compute the Stage-2 verifier score.

    S = w1*semantic + w2*responsibility + w3*skill + w4*title
        + w5*temporal_prior - w6*contradiction_penalty

    Similarity components contribute positively; the contradiction penalty is
    the only subtractive term.  All weights/thresholds/margins are configurable
    and versioned.  When semantic embeddings are unavailable the positive
    weights are renormalized (or zeroed) per ``semantic_unavailable_policy``.
    """
    merged = _identity_v2_config(config)
    contradiction = _contradiction_penalty(
        title_similarity=title_similarity,
        responsibility_similarity=responsibility_similarity,
        config=config,
    )
    semantic_status = "available" if semantic_similarity is not None else "unavailable"
    available: list[tuple[str, float, float]] = [
        ("semantic", float(merged["semantic_similarity_weight"]), semantic_similarity or 0.0),
        ("responsibility", float(merged["responsibility_similarity_weight"]), responsibility_similarity),
        ("skill", float(merged["skill_similarity_weight"]), skill_similarity),
        ("title", float(merged["title_similarity_weight"]), title_similarity),
        ("temporal_prior", float(merged["temporal_prior_weight"]), temporal_prior),
    ]
    if semantic_status == "unavailable" and str(merged["semantic_unavailable_policy"]) == "renormalize":
        available = [item for item in available if item[0] != "semantic"]
    total_weight = sum(weight for _, weight, _ in available)
    if total_weight <= 0:
        raise ValueError("identity v2 has no available positive weights")
    positive_score = (
        sum(value * weight for _, weight, value in available) / total_weight
    )
    final_score = round(
        positive_score - float(merged["contradiction_penalty_weight"]) * contradiction,
        6,
    )
    explanation = (
        f"semantic {semantic_status}; semantic_score "
        f"{None if semantic_similarity is None else round(semantic_similarity, 6)}; "
        f"responsibility_score {round(responsibility_similarity, 6)}; "
        f"skill_score {round(skill_similarity, 6)}; "
        f"title_score {round(title_similarity, 6)}; "
        f"temporal_prior {round(temporal_prior, 6)}; "
        f"contradiction_penalty {round(contradiction, 6)}; "
        f"final_score {final_score}"
    )
    return IdentityV2VerifierComponents(
        semantic_score=(
            round(semantic_similarity, 6) if semantic_similarity is not None else None
        ),
        responsibility_score=round(responsibility_similarity, 6),
        skill_score=round(skill_similarity, 6),
        title_score=round(title_similarity, 6),
        temporal_prior=round(temporal_prior, 6),
        contradiction_penalty=round(contradiction, 6),
        final_score=final_score,
        semantic_status=semantic_status,
        weights=tuple(float(merged[name]) for name in (
            "semantic_similarity_weight",
            "responsibility_similarity_weight",
            "skill_similarity_weight",
            "title_similarity_weight",
            "temporal_prior_weight",
            "contradiction_penalty_weight",
        )),
        explanation=explanation,
    )


def _d14_factor_values(
    current: CandidateIdentitySpec,
    candidate: CandidateIdentitySpec,
) -> tuple[
    float, float, float, float,
    float | None,
    CandidateIdentityComponents,
]:
    """Compute the exact D14 factor values for one candidate pair.

    This mirrors the locked D14 matcher's per-candidate computation so Stage 1
    candidate generation uses the same title/skill/responsibility/membership
    factors as ``match_selected_candidate_identity``.
    """
    current_titles = current.evidence_titles or current.titles
    current_skills = frozenset(
        _normalise_evidence_text(value)
        for value in (current.evidence_skills or current.skills)
        if _normalise_evidence_text(value)
    )
    current_responsibilities = current.evidence_responsibilities or current.responsibilities
    candidate_titles = candidate.evidence_titles or candidate.titles
    candidate_skills = frozenset(
        _normalise_evidence_text(value)
        for value in (candidate.evidence_skills or candidate.skills)
        if _normalise_evidence_text(value)
    )
    candidate_responsibilities = (
        candidate.evidence_responsibilities or candidate.responsibilities
    )
    title = _text_similarity(current_titles, candidate_titles)
    skill = _jaccard(current_skills, candidate_skills)
    responsibility = _text_similarity(current_responsibilities, candidate_responsibilities)
    sample = _optional_jaccard(current.member_evidence_ids, candidate.member_evidence_ids)
    dedup = _optional_jaccard(current.member_dedup_cluster_ids, candidate.member_dedup_cluster_ids)
    template = _optional_jaccard(
        current.member_template_cluster_ids, candidate.member_template_cluster_ids
    )
    available_overlaps = [value for value in (sample, dedup, template) if value is not None]
    membership = max(available_overlaps, default=0.0)
    semantic = _cosine(current.semantic_centroid, candidate.semantic_centroid) or None
    components = CandidateIdentityComponents(
        round(title, 6),
        round(skill, 6),
        round(responsibility, 6),
        round(membership, 6),
        round(semantic, 6) if semantic is not None else None,
        round(sample, 6) if sample is not None else None,
        round(dedup, 6) if dedup is not None else None,
        round(template, 6) if template is not None else None,
    )
    return title, skill, responsibility, membership, semantic, components


def _stage1_d14_generated(
    current: CandidateIdentitySpec,
    candidate: CandidateIdentitySpec,
    config: Mapping[str, object] | None = None,
) -> tuple[bool, tuple[str, ...]]:
    """D14 Stage-1 candidate generation: the locked D14 OR rule decides whether
    this historical candidate is a generated proposal for the current cluster."""
    title, skill, responsibility, membership, _semantic, _components = _d14_factor_values(
        current, candidate
    )
    # Stage 1 must always invoke the locked D14 rule under the D14 config
    # version, regardless of the Identity v2 config version declared by the
    # pipeline (which owns candidate_identity_config_version).
    d14_config = {
        **dict(config or {}),
        "candidate_identity_config_version": SELECTED_CONFIG_VERSION,
    }
    reasons = selected_identity_basis(
        title_similarity=title,
        skill_similarity=skill,
        responsibility_similarity=responsibility,
        membership_overlap=membership,
        config=d14_config,
    )
    return bool(reasons), reasons


def _identity_v2_hypotheses(
    current: CandidateIdentitySpec,
    candidates: tuple[CandidateIdentitySpec, ...],
    config: Mapping[str, object] | None = None,
    *,
    current_window_id: str | None = None,
    window_order: tuple[str, ...] | None = None,
    top_k: int = 1,
) -> list[CandidateIdentityMatch]:
    """Two-stage Identity v2 linker.

    Stage 1 reuses D14 candidate generation; Stage 2 runs the configurable
    semantic + temporal verifier with D16 abstention.
    """
    merged = _identity_v2_config(config)
    accept_threshold = float(merged["verifier_accept_threshold"])
    review_margin = float(merged["verifier_review_margin"])
    top2_margin = float(merged["verifier_top2_margin"])
    config_version = identity_v2_config_version(config)
    abstention_disabled = (
        str((config or {}).get("experiment_policy", ""))
        == "d5-short-window-frozen-no-threshold-tuning.v1"
        or str((config or {}).get("identity_abstention_mode", "enabled")).casefold()
        == "disabled"
    )
    if not candidates:
        return [
            CandidateIdentityMatch(
            candidate_id=None,
            identity_similarity=1.0,
            components=CandidateIdentityComponents(None, None, None, None, None),
            threshold=accept_threshold,
            matched=False,
            decision_reason="first observation creates candidate; no historical candidate matched",
            decision_version=IDENTITY_V2_DECISION_VERSION,
            config_version=config_version,
            semantic_status="unavailable",
            decision_basis=("no_historical_candidate",),
            margin=None,
            abstain=False,
            abstention_reason=None,
            )
        ]

    # Stage 1: D14 candidate generation over the historical registry.
    generated: list[
        tuple[
            float,
            CandidateIdentitySpec,
            CandidateIdentityComponents,
            tuple[str, ...],
            IdentityV2VerifierComponents,
        ]
    ] = []
    for candidate in sorted(candidates, key=lambda item: item.candidate_id or ""):
        title, skill, responsibility, membership, semantic, components = _d14_factor_values(
            current, candidate
        )
        generated_bool, reasons = _stage1_d14_generated(current, candidate, config)
        if not generated_bool:
            continue
        temporal_prior = _temporal_prior(
            last_seen_window_id=candidate.last_seen_window_id,
            current_window_id=current_window_id,
            window_order=window_order,
            config=config,
        )
        verifier = compute_identity_v2_verifier(
            semantic_similarity=semantic,
            responsibility_similarity=responsibility,
            skill_similarity=skill,
            title_similarity=title,
            temporal_prior=temporal_prior,
            config=config,
        )
        generated.append(
            (verifier.final_score, candidate, components, reasons, verifier)
        )

    if not generated:
        # D14 generated no candidate proposal: automatic new identity.
        return [
            CandidateIdentityMatch(
            candidate_id=None,
            identity_similarity=0.0,
            components=CandidateIdentityComponents(None, None, None, None, None),
            threshold=accept_threshold,
            matched=False,
            decision_reason="no D14 stage-1 candidate generated; closest historical candidate not proposed",
            decision_version=IDENTITY_V2_DECISION_VERSION,
            config_version=config_version,
            semantic_status="unavailable",
            decision_basis=("no_stage1_candidate_generated",),
            margin=None,
            abstain=False,
            abstention_reason=None,
            )
        ]

    generated.sort(
        key=lambda item: (item[0], item[1].candidate_id or ""), reverse=True
    )
    matches: list[CandidateIdentityMatch] = []
    for index, (
        best_score,
        best_candidate,
        best_components,
        best_reasons,
        best_verifier,
    ) in enumerate(generated[:top_k]):
        next_score = generated[index + 1][0] if index + 1 < len(generated) else None
        matched = best_score >= accept_threshold
        margin = round(best_score - accept_threshold, 6)
        top2_gap = (
            round(best_score - next_score, 6)
            if next_score is not None
            else None
        )
        abstain = False
        abstention_reason = None
        if matched:
            if margin <= review_margin:
                abstain = True
                abstention_reason = (
                    f"top{index + 1} final_score {best_score} is within "
                    f"{review_margin} of accept threshold {accept_threshold}"
                )
            elif top2_gap is not None and top2_gap <= top2_margin:
                abstain = True
                abstention_reason = (
                    f"top{index + 1}/top{index + 2} margin {top2_gap} is within "
                    f"{top2_margin} of the decision boundary"
                )
        else:
            if -margin <= review_margin:
                abstain = True
                abstention_reason = (
                    f"top{index + 1} final_score {best_score} is within "
                    f"{review_margin} below accept threshold {accept_threshold}"
                )
        if abstention_disabled:
            abstain = False
            abstention_reason = None

        if matched:
            decision = (
                f"identity v2 final_score {best_score} >= accept threshold "
                f"{accept_threshold}; stage-1 D14 reasons {', '.join(best_reasons)}; "
                f"{best_verifier.explanation}"
            )
        else:
            decision = (
                f"identity v2 final_score {best_score} < accept threshold "
                f"{accept_threshold}; closest generated candidate "
                f"{best_candidate.candidate_id}; stage-1 D14 reasons "
                f"{', '.join(best_reasons)}; {best_verifier.explanation}"
            )
        matches.append(
            CandidateIdentityMatch(
                candidate_id=best_candidate.candidate_id,
                identity_similarity=best_score,
                components=best_components,
                threshold=accept_threshold,
                matched=matched,
                decision_reason=decision,
                decision_version=IDENTITY_V2_DECISION_VERSION,
                config_version=config_version,
                semantic_status=best_verifier.semantic_status,
                decision_basis=(
                    ("verifier_accept",)
                    if matched
                    else ("verifier_reject",)
                ),
                margin=margin,
                abstain=abstain,
                abstention_reason=abstention_reason,
                verifier=best_verifier,
            )
        )
    return matches


def match_identity_v2(
    current: CandidateIdentitySpec,
    candidates: tuple[CandidateIdentitySpec, ...],
    config: Mapping[str, object] | None = None,
    *,
    current_window_id: str | None = None,
    window_order: tuple[str, ...] | None = None,
) -> CandidateIdentityMatch:
    return _identity_v2_hypotheses(
        current,
        candidates,
        config,
        current_window_id=current_window_id,
        window_order=window_order,
        top_k=1,
    )[0]




def select_candidate_identity(
    current: CandidateIdentitySpec,
    candidates: tuple[CandidateIdentitySpec, ...],
    config: Mapping[str, object] | None = None,
    *,
    current_window_id: str | None = None,
    window_order: tuple[str, ...] | None = None,
) -> CandidateIdentityMatch:
    """Dispatch an explicitly versioned linker; production defaults to D14."""
    version = str(
        (config or {}).get("candidate_identity_linker_version", SELECTED_DECISION_VERSION)
    )
    if version == LEGACY_DECISION_VERSION:
        return match_candidate_identity(current, candidates, config)  # type: ignore[arg-type]
    if version == SELECTED_DECISION_VERSION:
        return match_selected_candidate_identity(current, candidates, config)
    if version == IDENTITY_V2_DECISION_VERSION:
        return match_identity_v2(
            current,
            candidates,
            config,
            current_window_id=current_window_id,
            window_order=window_order,
        )
    raise ValueError(
        f"unsupported candidate identity linker version: {version}; "
        f"expected one of {sorted(SUPPORTED_LINKER_VERSIONS)}"
    )


def select_candidate_identity_hypotheses(
    current: CandidateIdentitySpec,
    candidates: tuple[CandidateIdentitySpec, ...],
    config: Mapping[str, object] | None = None,
    *,
    current_window_id: str | None = None,
    window_order: tuple[str, ...] | None = None,
    top_k: int = 3,
) -> tuple[CandidateIdentityMatch, ...]:
    """Return ranked historical hypotheses from the configured identity linker.

    The first hypothesis is identical to ``select_candidate_identity``. Ranked
    hypotheses reuse the same computed evidence and formal abstention rules; no
    threshold is added or adjusted here.
    """
    version = str(
        (config or {}).get("candidate_identity_linker_version", SELECTED_DECISION_VERSION)
    )
    if version == LEGACY_DECISION_VERSION:
        return tuple(
            _legacy_candidate_hypotheses(current, candidates, config, top_k=top_k)
        )
    if version == SELECTED_DECISION_VERSION:
        return tuple(
            _selected_candidate_hypotheses(current, candidates, config, top_k=top_k)
        )
    if version == IDENTITY_V2_DECISION_VERSION:
        return tuple(
            _identity_v2_hypotheses(
                current,
                candidates,
                config,
                current_window_id=current_window_id,
                window_order=window_order,
                top_k=top_k,
            )
        )
    raise ValueError(
        f"unsupported candidate identity linker version: {version}; "
        f"expected one of {sorted(SUPPORTED_LINKER_VERSIONS)}"
    )
