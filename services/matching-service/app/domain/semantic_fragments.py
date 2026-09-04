"""Deterministic, Evidence-bound semantic fragmentation of authoritative Profiles."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.domain.profiles import CVMatchProfile, Evidence, PositionMatchProfile
from app.domain.vector_contracts import (
    CV_FRAGMENT_TYPES,
    POSITION_FRAGMENT_TYPES,
    SEMANTIC_FRAGMENT_MAX_CHARS,
    SemanticFragment,
)

FRAGMENT_ID_NAMESPACE = UUID("5b95ac53-f305-53ba-a23b-f2a804d91fc9")
_WHITESPACE = re.compile(r"\s+")
_CJK = re.compile(r"[\u3400-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")


class SemanticFragmentationViolation(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FragmentVersionPlan:
    current: tuple[SemanticFragment, ...]
    superseded: tuple[SemanticFragment, ...]
    unchanged: tuple[SemanticFragment, ...]


@dataclass(frozen=True)
class _Candidate:
    fragment_type: str
    source_object_id: str
    evidence: Evidence


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFC", text)).strip()


def _language(text: str) -> Literal["zh-Hans", "en", "und"]:
    if _CJK.search(text):
        return "zh-Hans"
    if _LATIN.search(text):
        return "en"
    return "und"


def _evidence_key(evidence: Evidence) -> tuple[str, int, int, int]:
    return (
        evidence.source_id,
        evidence.start if evidence.start is not None else -1,
        evidence.end if evidence.end is not None else -1,
        evidence.occurrence_index if evidence.occurrence_index is not None else -1,
    )


def _candidate(
    fragment_type: str,
    source_object_id: str,
    evidence_refs: Iterable[Evidence],
) -> tuple[_Candidate, ...]:
    return tuple(
        _Candidate(fragment_type, source_object_id, evidence)
        for evidence in evidence_refs
    )


def _matched_candidates(
    fragment_type: str,
    source_object_id: str,
    values: Iterable[str | None],
    evidence_refs: Iterable[Evidence],
) -> tuple[_Candidate, ...]:
    normalized_values = {_normalize(value) for value in values if value}
    return tuple(
        _Candidate(fragment_type, source_object_id, evidence)
        for evidence in evidence_refs
        if _normalize(evidence.quote) in normalized_values
    )


def _cv_candidates(profile: CVMatchProfile) -> tuple[_Candidate, ...]:
    items: list[_Candidate] = []
    for feature in profile.match_features:
        feature_type = {
            "self_evaluation": "cv_summary",
            "award": "achievement",
            "skill": "skill_context",
            "task": "work_experience",
        }.get(feature.feature_type)
        if feature_type is not None:
            items.extend(
                _candidate(feature_type, feature.feature_id, feature.evidence_refs)
            )
    for skill in profile.skills:
        items.extend(
            _candidate("skill_context", skill.aggregation_key, skill.evidence_refs)
        )
    for experience in profile.work_experiences:
        items.extend(
            _matched_candidates(
                "work_experience",
                experience.experience_id,
                (experience.role, *experience.responsibilities),
                experience.evidence_refs,
            )
        )
        items.extend(
            _matched_candidates(
                "scenario_evidence",
                experience.experience_id,
                experience.business_scenarios,
                experience.evidence_refs,
            )
        )
    for project in profile.projects:
        items.extend(
            _matched_candidates(
                "project", project.experience_id, (project.role,), project.evidence_refs
            )
        )
        items.extend(
            _matched_candidates(
                "project_responsibility",
                project.experience_id,
                project.responsibilities,
                project.evidence_refs,
            )
        )
        items.extend(
            _matched_candidates(
                "scenario_evidence",
                project.experience_id,
                project.business_scenarios,
                project.evidence_refs,
            )
        )
    for education in profile.education:
        items.extend(
            _candidate("education_context", education.education_id, education.evidence_refs)
        )
    return tuple(items)


def _position_candidates(profile: PositionMatchProfile) -> tuple[_Candidate, ...]:
    items: list[_Candidate] = []
    items.extend(
        _matched_candidates(
            "position_summary",
            profile.position_id,
            (profile.canonical_title,),
            profile.evidence_refs,
        )
    )
    items.extend(
        _matched_candidates(
            "responsibility",
            profile.position_id,
            profile.core_responsibilities,
            profile.evidence_refs,
        )
    )
    for requirement in profile.required_skills:
        object_id = requirement.skill_id or requirement.canonical_name or "unresolved"
        items.extend(
            _candidate("required_skill_context", object_id, requirement.evidence_refs)
        )
    for requirement in profile.preferred_skills:
        object_id = requirement.skill_id or requirement.canonical_name or "unresolved"
        items.extend(
            _candidate("preferred_skill_context", object_id, requirement.evidence_refs)
        )
    for condition in profile.hard_conditions:
        fragment_type = {
            "education": "education_requirement",
            "experience": "experience_requirement",
        }.get(condition.condition_type)
        if fragment_type is not None:
            items.extend(
                _candidate(fragment_type, condition.condition_id, condition.evidence_refs)
            )
    items.extend(
        _matched_candidates(
            "scenario_requirement",
            f"{profile.position_id}:scenario",
            profile.business_scenarios.values,
            profile.business_scenarios.evidence_refs,
        )
    )
    return tuple(items)


def _build_fragments(
    *,
    tenant_ref: str,
    source_type: Literal["cv", "position"],
    target_type: Literal["candidate_cv", "standard_position", "enterprise_job"],
    source_id: str,
    source_version: str,
    source_profile_id: str,
    taxonomy_version: str,
    graph_version: str | None,
    candidates: tuple[_Candidate, ...],
) -> tuple[SemanticFragment, ...]:
    ordered = sorted(
        candidates,
        key=lambda item: (
            item.fragment_type,
            item.source_object_id,
            _evidence_key(item.evidence),
        ),
    )
    fragments: list[SemanticFragment] = []
    used_evidence: set[tuple[str, int, int, int]] = set()
    for candidate in ordered:
        evidence_key = _evidence_key(candidate.evidence)
        if evidence_key in used_evidence:
            continue
        normalized_text = _normalize(candidate.evidence.quote)
        if not normalized_text:
            raise SemanticFragmentationViolation(
                "SEMANTIC_FRAGMENT_EMPTY", "Evidence produced an empty semantic fragment"
            )
        if len(normalized_text) > SEMANTIC_FRAGMENT_MAX_CHARS:
            raise SemanticFragmentationViolation(
                "SEMANTIC_FRAGMENT_TOO_LONG",
                f"semantic fragment exceeds {SEMANTIC_FRAGMENT_MAX_CHARS} characters",
            )
        stable_key = (
            f"{source_type}:{target_type}:{source_id}:"
            f"{candidate.fragment_type}:"
            f"{candidate.source_object_id}:{evidence_key}"
        )
        fragments.append(
            SemanticFragment(
                tenant_ref=tenant_ref,
                fragment_id=stable_key,
                source_type=source_type,
                target_type=target_type,
                source_id=source_id,
                source_version=source_version,
                source_profile_id=source_profile_id,
                fragment_type=candidate.fragment_type,
                normalized_text=normalized_text,
                evidence_ref=candidate.evidence,
                language=_language(normalized_text),
                sequence=len(fragments),
                taxonomy_version=taxonomy_version,
                graph_version=graph_version,
            )
        )
        used_evidence.add(evidence_key)
    return tuple(fragments)


def fragment_cv_profile(
    profile: CVMatchProfile, *, tenant_ref: str
) -> tuple[SemanticFragment, ...]:
    return _build_fragments(
        tenant_ref=tenant_ref,
        source_type="cv",
        target_type="candidate_cv",
        source_id=profile.cv_id,
        source_version=profile.source_version,
        source_profile_id=profile.profile_version or profile.source_version,
        taxonomy_version=profile.taxonomy_version,
        graph_version=None,
        candidates=_cv_candidates(profile),
    )


def fragment_position_profile(
    profile: PositionMatchProfile,
    *,
    tenant_ref: str,
    target_type: Literal["standard_position", "enterprise_job"] = "standard_position",
) -> tuple[SemanticFragment, ...]:
    return _build_fragments(
        tenant_ref=tenant_ref,
        source_type="position",
        target_type=target_type,
        source_id=profile.position_id,
        source_version=profile.source_version,
        source_profile_id=profile.profile_version or profile.source_version,
        taxonomy_version=profile.taxonomy_version,
        graph_version=profile.graph_version,
        candidates=_position_candidates(profile),
    )


def plan_fragment_version_change(
    previous: tuple[SemanticFragment, ...],
    current: tuple[SemanticFragment, ...],
) -> FragmentVersionPlan:
    def content_identity(fragment: SemanticFragment) -> tuple:
        return (
            fragment.schema_version,
            fragment.source_type,
            fragment.target_type,
            fragment.source_id,
            fragment.fragment_type,
            fragment.normalized_text,
            fragment.evidence_ref.model_dump(mode="json"),
            fragment.language,
            fragment.taxonomy_version,
            fragment.graph_version,
        )

    previous_by_id = {item.fragment_id: item for item in previous}
    current_by_id = {item.fragment_id: item for item in current}
    unchanged_ids = {
        fragment_id
        for fragment_id in previous_by_id.keys() & current_by_id.keys()
        if content_identity(previous_by_id[fragment_id])
        == content_identity(current_by_id[fragment_id])
    }
    superseded_ids = previous_by_id.keys() - unchanged_ids
    current_ids = current_by_id.keys() - unchanged_ids
    return FragmentVersionPlan(
        current=tuple(current_by_id[item] for item in sorted(current_ids)),
        superseded=tuple(previous_by_id[item] for item in sorted(superseded_ids)),
        unchanged=tuple(current_by_id[item] for item in sorted(unchanged_ids)),
    )


__all__ = [
    "CV_FRAGMENT_TYPES",
    "POSITION_FRAGMENT_TYPES",
    "FragmentVersionPlan",
    "SemanticFragmentationViolation",
    "fragment_cv_profile",
    "fragment_position_profile",
    "plan_fragment_version_change",
]
