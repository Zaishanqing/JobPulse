"""Versioned admission policy for clusters entering the emerging candidate pool.

The policy intentionally returns a categorical decision instead of a boolean.
``REJECT_OFF_TARGET`` means the cluster is not admitted as candidate evidence;
``WEAK_EVIDENCE`` and ``REVIEW_REQUIRED`` keep the existing weak lifecycle state
but do not automatically promote to a strong emerging state.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from app.domain.values import FrozenDict, freeze


ADMISSION_POLICY_VERSION = "candidate-admission-policy.v2"
ADMISSION_CERTIFICATE_SCHEMA_VERSION = "candidate-admission-certificate.v1"
ADMISSION_DECISIONS = (
    "ADMIT",
    "WEAK_EVIDENCE",
    "REJECT_OFF_TARGET",
    "REVIEW_REQUIRED",
)
DEFAULT_POLICY_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "candidate_admission_policy.v2.json"
)


@dataclass(frozen=True)
class AdmissionEvidence:
    titles: tuple[str, ...]
    responsibilities: tuple[str, ...]
    skills: tuple[str, ...]


@dataclass(frozen=True)
class AdmissionDecision:
    title_score: float
    responsibility_score: float
    skill_score: float
    combined_score: float
    decision: str
    decision_reason: str
    policy_version: str
    config_version: str
    canonical_role: str | None
    evidence_basis: tuple[str, ...] = ()
    skill_only_weak: bool = False

    def certificate(self) -> dict[str, object]:
        return {
            "schema_version": ADMISSION_CERTIFICATE_SCHEMA_VERSION,
            "policy_version": self.policy_version,
            "config_version": self.config_version,
            "title_score": round(self.title_score, 6),
            "responsibility_score": round(self.responsibility_score, 6),
            "skill_score": round(self.skill_score, 6),
            "combined_score": round(self.combined_score, 6),
            "decision": self.decision,
            "decision_reason": self.decision_reason,
            "canonical_role": self.canonical_role,
            "evidence_basis": list(self.evidence_basis),
            "skill_only_weak": self.skill_only_weak,
        }


def _normalise(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value.casefold())


def _text_contains(term: str, value: str) -> bool:
    normalised_term = _normalise(term)
    normalised_value = _normalise(value)
    if not normalised_term or not normalised_value:
        return False
    has_cjk = any("\u4e00" <= char <= "\u9fff" for char in normalised_term)
    if has_cjk or len(normalised_term) >= 4:
        return normalised_term in normalised_value
    return normalised_term in set(re.findall(r"[a-z0-9]+", normalised_value))


def _profile_support(
    texts: tuple[str, ...], terms: tuple[str, ...]
) -> float:
    if not texts or not terms:
        return 0.0
    return max(
        (
            1.0
            for value in texts
            for term in terms
            if _text_contains(term, value)
        ),
        default=0.0,
    )


def _has_ai_signal(
    skills: tuple[str, ...], signals: tuple[str, ...]
) -> bool:
    return any(
        _text_contains(signal, skill) for skill in skills for signal in signals
    )


def load_candidate_admission_policy(
    path: str | Path | None = None,
) -> dict[str, Any]:
    resolved = Path(path) if path else DEFAULT_POLICY_PATH
    if not resolved.is_file():
        raise FileNotFoundError(f"candidate admission policy file is missing: {resolved}")
    data = json.loads(resolved.read_text(encoding="utf-8"))
    if str(data.get("policy_version")) != ADMISSION_POLICY_VERSION:
        raise ValueError(
            "candidate admission policy file does not declare "
            f"{ADMISSION_POLICY_VERSION}"
        )
    return data


def _policy_weights(policy: Mapping[str, Any]) -> dict[str, float]:
    weights = {
        str(key): float(value)
        for key, value in (policy.get("weights") or {}).items()
    }
    if set(weights) != {"title", "responsibility", "skill"}:
        raise ValueError("candidate admission weights must define title/responsibility/skill")
    if any(value < 0 for value in weights.values()):
        raise ValueError("candidate admission weights must be non-negative")
    if abs(sum(weights.values()) - 1.0) > 1e-9:
        raise ValueError("candidate admission weights must sum to one")
    return weights


def _profile_score(
    evidence: AdmissionEvidence, profile: Mapping[str, Any]
) -> tuple[float, float, float]:
    titles = tuple(str(value) for value in evidence.titles if str(value).strip())
    responsibilities = tuple(
        str(value) for value in evidence.responsibilities if str(value).strip()
    )
    skills = tuple(str(value) for value in evidence.skills if str(value).strip())
    title_terms = tuple(str(value) for value in (profile.get("title_terms") or ()))
    responsibility_terms = tuple(
        str(value) for value in (profile.get("responsibility_terms") or ())
    )
    skill_terms = tuple(str(value) for value in (profile.get("skill_terms") or ()))
    return (
        _profile_support(titles, title_terms),
        _profile_support(responsibilities, responsibility_terms),
        _profile_support(skills, skill_terms),
    )


def assess_candidate_admission(
    evidence: AdmissionEvidence,
    policy: Mapping[str, Any],
    canonical_role_id: str | None = None,
) -> AdmissionDecision:
    weights = _policy_weights(policy)
    thresholds = {
        str(key): float(value) for key, value in (policy.get("thresholds") or {}).items()
    }
    for key in (
        "admit_combined",
        "review_combined",
        "title_support",
        "responsibility_support",
    ):
        if key not in thresholds:
            raise ValueError(f"candidate admission threshold is missing: {key}")

    profiles = list(policy.get("role_profiles") or ())
    if canonical_role_id is not None:
        selected = next(
            (profile for profile in profiles if profile.get("position_id") == canonical_role_id),
            None,
        )
        if selected is None:
            raise ValueError(f"unknown canonical role profile: {canonical_role_id}")
        candidates = [(selected, _profile_score(evidence, selected))]
    else:
        candidates = [
            (profile, _profile_score(evidence, profile)) for profile in profiles
        ]
    if not candidates:
        return AdmissionDecision(
            title_score=0.0,
            responsibility_score=0.0,
            skill_score=0.0,
            combined_score=0.0,
            decision="REVIEW_REQUIRED",
            decision_reason="no canonical emerging role profile is configured",
            policy_version=str(policy.get("policy_version")),
            config_version=str(policy.get("config_version")),
            canonical_role=None,
            evidence_basis=("no_role_profile",),
        )

    selected_profile, (title_score, responsibility_score, skill_score) = max(
        candidates,
        key=lambda item: (
            item[1][0] * weights["title"]
            + item[1][1] * weights["responsibility"]
            + item[1][2] * weights["skill"],
            item[0].get("position_id") or "",
        ),
    )
    combined = round(
        title_score * weights["title"]
        + responsibility_score * weights["responsibility"]
        + skill_score * weights["skill"],
        6,
    )
    signals = tuple(policy.get("skill_only_ai_signals") or ())
    has_signal = _has_ai_signal(evidence.skills, signals)
    skill_only_weak = (
        has_signal
        and title_score < thresholds["title_support"]
        and responsibility_score < thresholds["responsibility_support"]
    )
    admit_threshold = thresholds["admit_combined"]
    review_threshold = thresholds["review_combined"]

    if skill_only_weak:
        if combined >= admit_threshold:
            decision = "REVIEW_REQUIRED"
        elif title_score == 0.0 and responsibility_score == 0.0:
            decision = "REJECT_OFF_TARGET"
        else:
            decision = "WEAK_EVIDENCE"
    elif combined >= admit_threshold:
        decision = "ADMIT"
    elif combined >= review_threshold:
        decision = "REVIEW_REQUIRED"
    else:
        decision = "REJECT_OFF_TARGET"

    basis = [
        f"title={round(title_score, 6)}",
        f"responsibility={round(responsibility_score, 6)}",
        f"skill={round(skill_score, 6)}",
        f"combined={round(combined, 6)}",
    ]
    if has_signal:
        basis.append("ai_skill_signal")
    if skill_only_weak:
        basis.append("skill_only_weak")
    reason = (
        f"canonical role {selected_profile.get('position_id')}; "
        + "; ".join(basis)
        + f"; decision={decision}"
    )
    return AdmissionDecision(
        title_score=round(title_score, 6),
        responsibility_score=round(responsibility_score, 6),
        skill_score=round(skill_score, 6),
        combined_score=combined,
        decision=decision,
        decision_reason=reason,
        policy_version=str(policy.get("policy_version")),
        config_version=str(policy.get("config_version")),
        canonical_role=str(selected_profile.get("position_id")),
        evidence_basis=tuple(basis),
        skill_only_weak=skill_only_weak,
    )


def cluster_admission_evidence(cluster: Any) -> AdmissionEvidence:
    generated = cluster.assessment.generated_definition
    titles = tuple(cluster.representative_titles) or (str(generated.position_name),)
    responsibilities = tuple(cluster.core_responsibilities) or tuple(
        generated.core_responsibilities
    )
    skills = tuple(cluster.core_skills) or tuple(
        str(item.raw_skill) for item in generated.required_skills
    )
    return AdmissionEvidence(titles, responsibilities, skills)


def attach_cluster_admission_certificates(
    clusters: list[Any], policy: Mapping[str, Any]
) -> list[Any]:
    attached = []
    for cluster in clusters:
        decision = assess_candidate_admission(cluster_admission_evidence(cluster), policy)
        evidence = dict(cluster.assessment.evidence_package or {})
        evidence["admission_certificate"] = decision.certificate()
        frozen_evidence = FrozenDict(
            {key: freeze(value) for key, value in evidence.items()}
        )
        assessment = replace(
            cluster.assessment,
            evidence_package=frozen_evidence,
        )
        attached.append(replace(cluster, assessment=assessment))
    return attached
