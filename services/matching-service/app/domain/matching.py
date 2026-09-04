"""Pure deterministic hard-constraint and exact-skill matching rules."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Literal

from app.domain.context_matching import (
    ContextMatchingConfig,
    context_coverage,
    evaluate_projects,
    evaluate_responsibilities,
    evaluate_scenarios,
)
from app.domain.degree_levels import (
    DEGREE_LEVELS,
    degree_rank,
    normalize_degree,
)
from app.domain.evaluation import (
    EvaluationSummary,
    HardConstraintResult,
    MatchEvaluation,
    SkillResult,
)
from app.domain.profiles import (
    CapabilityProfile,
    CVMatchProfile,
    Evidence,
    HardCondition,
    PositionMatchProfile,
    PositionSkillRequirement,
)
from app.domain.relation_matching import (
    SkillRelationMatchingConfig,
    apply_skill_relations,
    transferable_coverage,
)
from app.domain.requirement_graph import (
    SpecialtyRouteSelection,
    apply_effective_required_set,
    evaluate_requirement_graph,
    select_specialty_route,
)
from app.domain.scoring import score_match_evaluation
from app.domain.skill_relations import SkillRelation

HARD_CONSTRAINT_TYPES = (
    "education",
    "experience",
    "certificate",
    "language",
    "location",
    "availability",
)
ConstraintType = Literal[
    "education", "experience", "certificate", "language", "location", "availability"
]


@dataclass(frozen=True)
class MatchingAlgorithmConfig:
    algorithm_version: str = "deterministic-matching.v9"
    capability_levels: tuple[str, ...] = (
        "unknown",
        "basic",
        "working",
        "proficient",
        "advanced",
        "expert",
    )
    education_levels: tuple[str, ...] = DEGREE_LEVELS
    language_levels: tuple[str, ...] = (
        "unknown",
        "basic",
        "conversational",
        "professional",
        "fluent",
        "native",
    )
    partially_supported_confidence_factor: float = 0.75
    context: ContextMatchingConfig = ContextMatchingConfig()
    relations: SkillRelationMatchingConfig = SkillRelationMatchingConfig()


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip().casefold()


def _joined(values: tuple[str, ...]) -> str | None:
    return " | ".join(sorted({_normalized(item) for item in values})) or None


def _evidence(items: tuple[object, ...]) -> tuple[Evidence, ...]:
    refs = {
        (
            ref.source_id,
            ref.quote,
            ref.start,
            ref.end,
            ref.alignment,
            ref.occurrence_index,
        ): ref
        for item in items
        for ref in getattr(item, "evidence_refs", ())
    }
    return tuple(refs[key] for key in sorted(refs, key=str))


def _result(
    condition: HardCondition,
    status: str,
    candidate_value: str | None,
    candidate_evidence: tuple[Evidence, ...],
    reason_code: str,
    confidence: float,
) -> HardConstraintResult:
    return HardConstraintResult(
        requirement_id=condition.condition_id,
        constraint_type=condition.condition_type,
        status=status,
        required_value=condition.value,
        candidate_value=candidate_value,
        position_evidence=condition.evidence_refs,
        candidate_evidence=candidate_evidence,
        reason_code=reason_code,
        confidence=confidence,
    )


def _not_required(constraint_type: ConstraintType) -> HardConstraintResult:
    return HardConstraintResult(
        requirement_id=f"not_required:{constraint_type}",
        constraint_type=constraint_type,
        status="not_required",
        required_value=None,
        candidate_value=None,
        reason_code="CONSTRAINT_NOT_REQUIRED",
        confidence=1.0,
    )


def _rank(value: str, levels: tuple[str, ...]) -> int | None:
    try:
        return levels.index(_normalized(value))
    except ValueError:
        return None


def _compare_scalar(condition: HardCondition, candidates: tuple[str, ...]) -> bool:
    required = tuple(
        _normalized(part)
        for part in re.split(r"\s*[|,]\s*", condition.value)
        if part.strip()
    )
    candidate_set = {_normalized(item) for item in candidates}
    if condition.operator == "one_of":
        return bool(candidate_set.intersection(required))
    return bool(candidate_set) and _normalized(condition.value) in candidate_set


def _compare_degree_scalar(
    condition: HardCondition, candidates: tuple[str, ...]
) -> bool:
    required = tuple(
        normalized
        for part in re.split(r"\s*[|,]\s*", condition.value)
        if part.strip() and (normalized := normalize_degree(part)) is not None
    )
    candidate_levels = {
        normalized
        for item in candidates
        if (normalized := normalize_degree(item)) is not None
    }
    return bool(candidate_levels.intersection(required))


def _education(
    condition: HardCondition,
    cv: CVMatchProfile,
    config: MatchingAlgorithmConfig,
) -> HardConstraintResult:
    if condition.resolution_status != "resolved":
        return _result(condition, "unresolved", None, (), "REQUIREMENT_UNRESOLVED", 0.0)
    if not cv.education:
        # 字段不存在：无法判断学历是否满足，属于信息不足，而不是明确不满足。
        return _result(
            condition,
            "unknown",
            None,
            (),
            "EDUCATION_NOT_OBSERVED",
            0.0,
        )
    resolved = tuple(item for item in cv.education if item.resolution_status == "resolved")
    has_unresolved = any(item.resolution_status != "resolved" for item in cv.education)
    if not resolved and any(item.resolution_status != "resolved" for item in cv.education):
        return _result(
            condition, "unresolved", None, _evidence(cv.education),
            "CANDIDATE_VALUE_UNRESOLVED", 0.0,
        )
    ranked_education = tuple(
        (item, degree_rank(item.degree_level))
        for item in resolved
        if item.degree_level
        and normalize_degree(item.degree_level) is not None
    )
    values = tuple(item.degree_level for item, _ in ranked_education)
    if not values:
        return _result(
            condition, "unknown", None, _evidence(resolved), "CANDIDATE_VALUE_UNKNOWN", 0.0
        )
    required_rank = degree_rank(condition.value)
    candidate_ranks = tuple(rank for _, rank in ranked_education)
    if required_rank is None or any(item is None for item in candidate_ranks):
        return _result(
            condition,
            "unresolved",
            _joined(values),
            _evidence(tuple(item for item, _ in ranked_education)),
            "CANDIDATE_VALUE_UNRESOLVED", 0.0,
        )
    qualifying = tuple(
        (item, rank)
        for item, rank in ranked_education
        if rank is not None
        and (
            rank >= required_rank
            if condition.operator == "at_least"
            else _compare_degree_scalar(condition, (item.degree_level,))
        )
    )
    if not qualifying:
        if has_unresolved:
            return _result(
                condition,
                "unresolved",
                _joined(values),
                _evidence(tuple(item for item, _ in ranked_education)),
                "CANDIDATE_VALUE_UNRESOLVED",
                0.0,
            )
        return _result(
            condition,
            "fail",
            _joined(values),
            _evidence(tuple(item for item, _ in ranked_education)),
            "CONSTRAINT_NOT_SATISFIED",
            1.0,
        )
    obtained = tuple(
        item
        for item, _ in qualifying
        if item.degree_status in {"obtained", "unknown"}
    )
    if obtained:
        best = max(
            obtained,
            key=lambda item: degree_rank(item.degree_level) or 0,
        )
        completion_confidence = (
            1.0
            if all(item.degree_status == "obtained" for item in obtained)
            else 0.7
        )
        return _result(
            condition,
            "pass",
            _joined((best.degree_level,)),
            _evidence((best,)),
            "CONSTRAINT_SATISFIED",
            completion_confidence,
        )
    pending_statuses = {item.degree_status for item, _ in qualifying}
    if pending_statuses.intersection({"enrolled", "expected", "future"}):
        return _result(
            condition,
            "fail",
            _joined(values),
            _evidence(tuple(item for item, _ in qualifying)),
            "DEGREE_NOT_YET_OBTAINED",
            1.0,
        )
    return _result(
        condition,
        "unknown",
        _joined(values),
        _evidence(tuple(item for item, _ in qualifying)),
        "DEGREE_COMPLETION_UNKNOWN",
        0.0,
    )


def _experience(condition: HardCondition, cv: CVMatchProfile) -> HardConstraintResult:
    if condition.resolution_status != "resolved":
        return _result(condition, "unresolved", None, (), "REQUIREMENT_UNRESOLVED", 0.0)
    required = _required_months(condition.value)
    if required is None or condition.operator != "at_least":
        return _result(
            condition, "unresolved", None, (), "REQUIREMENT_VALUE_UNSUPPORTED", 0.0
        )
    if required <= 0:
        # 0 年/0 个月门槛对任何候选人平凡满足，与 CV 字段是否存在无关。
        return _result(
            condition,
            "pass",
            "0.00-0.00 months",
            (),
            "CONSTRAINT_SATISFIED",
            1.0,
        )
    if not cv.work_experiences:
        # 字段不存在：无法核验工作年限，属于信息不足，而不是明确不满足。
        return _result(
            condition,
            "unknown",
            None,
            (),
            "EXPERIENCE_NOT_OBSERVED",
            0.0,
        )
    complete: list[tuple[date, date]] = []
    open_started: list[date] = []
    for item in cv.work_experiences:
        effective_end = item.end_date or (cv.as_of_date if item.is_current else None)
        if item.start_date is None or effective_end is None:
            if item.start_date is not None:
                open_started.append(item.start_date)
            continue
        complete.append((item.start_date, effective_end))
    lower = _merge_months(tuple(complete))
    upper_periods = list(complete)
    upper_periods.extend((start, cv.as_of_date) for start in open_started)
    upper = _merge_months(tuple(upper_periods))
    candidate = f"{lower:.2f}-{upper:.2f} months"
    supporting_experience = tuple(
        item
        for item in cv.work_experiences
        if item.start_date is not None and item.end_date is not None
    )
    refs = _evidence(supporting_experience)
    if lower >= required:
        return _result(
            condition, "pass", candidate, refs, "CONSTRAINT_SATISFIED", 1.0
        )
    if upper < required:
        return _result(
            condition,
            "fail",
            candidate,
            refs,
            "EXPERIENCE_MAXIMUM_BELOW_REQUIRED",
            1.0,
        )
    return _result(
        condition,
        "unknown",
        candidate,
        refs,
        "EXPERIENCE_BOUNDS_UNCERTAIN",
        0.0,
    )


def _required_months(value: str) -> float | None:
    normalized = unicodedata.normalize("NFKC", value).strip()
    match = re.fullmatch(
        r"\s*(\d+(?:\.\d+)?)\s*(?:[-~至到]\s*(\d+(?:\.\d+)?)\s*)?"
        r"(years?|months?|年|个月|月)\s*(?:以上|及以上|\+|or\s+more)?\s*",
        normalized,
        re.I,
    )
    if match is None:
        return None
    amount = float(match.group(1))
    unit = match.group(3).casefold()
    return amount * 12 if unit in {"year", "years", "年"} else amount


def _merge_months(periods: tuple[tuple[date, date], ...]) -> float:
    if not periods:
        return 0.0
    merged: list[tuple[date, date]] = []
    for start, end in sorted(periods):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        elif end > merged[-1][1]:
            merged[-1] = (merged[-1][0], end)
    days = sum((end - start).days for start, end in merged)
    return days / 30.4375




def _collection_constraint(
    condition: HardCondition,
    items: tuple[object, ...],
    value_attribute: str,
) -> HardConstraintResult:
    if condition.resolution_status != "resolved":
        return _result(condition, "unresolved", None, (), "REQUIREMENT_UNRESOLVED", 0.0)
    resolved = tuple(item for item in items if item.resolution_status == "resolved")
    values = tuple(str(getattr(item, value_attribute)) for item in resolved)
    has_unresolved = any(item.resolution_status != "resolved" for item in items)
    if not resolved and has_unresolved:
        return _result(
            condition, "unresolved", None, _evidence(items),
            "CANDIDATE_VALUE_UNRESOLVED", 0.0,
        )
    if not values:
        return _result(condition, "unknown", None, (), "CANDIDATE_VALUE_UNKNOWN", 0.0)
    passed = _compare_scalar(condition, values)
    if not passed and has_unresolved:
        return _result(
            condition,
            "unresolved",
            _joined(values),
            _evidence(items),
            "CANDIDATE_VALUE_UNRESOLVED",
            0.0,
        )
    return _result(
        condition,
        "pass" if passed else "fail",
        _joined(values),
        _evidence(resolved),
        "CONSTRAINT_SATISFIED" if passed else "CONSTRAINT_NOT_SATISFIED",
        1.0,
    )


def _language(
    condition: HardCondition,
    cv: CVMatchProfile,
    config: MatchingAlgorithmConfig,
) -> HardConstraintResult:
    if condition.resolution_status != "resolved":
        return _result(condition, "unresolved", None, (), "REQUIREMENT_UNRESOLVED", 0.0)
    parts = condition.value.split(":", maxsplit=1)
    required_code = _normalized(parts[0])
    required_level = parts[1] if len(parts) == 2 else None
    candidates = tuple(
        item for item in cv.languages if _normalized(item.language_code) == required_code
    )
    if any(item.resolution_status != "resolved" for item in candidates):
        return _result(
            condition, "unresolved", None, _evidence(candidates),
            "CANDIDATE_VALUE_UNRESOLVED", 0.0,
        )
    if not candidates:
        if not cv.languages:
            return _result(
                condition, "unknown", None, (), "CANDIDATE_VALUE_UNKNOWN", 0.0
            )
        return _result(
            condition, "fail", _joined(tuple(item.language_code for item in cv.languages)),
            _evidence(cv.languages), "CONSTRAINT_NOT_SATISFIED", 1.0,
        )
    values = tuple(
        f"{item.language_code}:{item.proficiency or 'unknown'}" for item in candidates
    )
    if required_level is None:
        return _result(
            condition, "pass", _joined(values), _evidence(candidates),
            "CONSTRAINT_SATISFIED", 1.0,
        )
    required_rank = _rank(required_level, config.language_levels)
    candidate_ranks = tuple(
        _rank(item.proficiency or "unknown", config.language_levels) for item in candidates
    )
    if required_rank is None or any(item is None for item in candidate_ranks):
        return _result(
            condition, "unresolved", _joined(values), _evidence(candidates),
            "CANDIDATE_VALUE_UNRESOLVED", 0.0,
        )
    best = max(item for item in candidate_ranks if item is not None)
    status = "pass" if best >= required_rank else "partial"
    return _result(
        condition,
        status,
        _joined(values),
        _evidence(candidates),
        "CONSTRAINT_SATISFIED" if status == "pass" else "CONSTRAINT_PARTIALLY_SATISFIED",
        1.0 if status == "pass" else 0.75,
    )


def _feature_constraint(
    condition: HardCondition,
    cv: CVMatchProfile,
    feature_type: Literal["location", "availability"],
) -> HardConstraintResult:
    if condition.resolution_status != "resolved":
        return _result(condition, "unresolved", None, (), "REQUIREMENT_UNRESOLVED", 0.0)
    features = tuple(item for item in cv.match_features if item.feature_type == feature_type)
    resolved = tuple(item for item in features if item.resolution_status == "resolved")
    has_unresolved = any(item.resolution_status != "resolved" for item in features)
    if not resolved and features:
        return _result(
            condition, "unresolved", None, _evidence(features),
            "CANDIDATE_VALUE_UNRESOLVED", 0.0,
        )
    values = tuple(item.canonical_name or item.raw_text for item in resolved)
    if not values:
        return _result(condition, "unknown", None, (), "CANDIDATE_VALUE_UNKNOWN", 0.0)
    passed = _compare_scalar(condition, values)
    if not passed and has_unresolved:
        return _result(
            condition,
            "unresolved",
            _joined(values),
            _evidence(features),
            "CANDIDATE_VALUE_UNRESOLVED",
            0.0,
        )
    return _result(
        condition,
        "pass" if passed else "fail",
        _joined(values),
        _evidence(resolved),
        "CONSTRAINT_SATISFIED" if passed else "CONSTRAINT_NOT_SATISFIED",
        1.0,
    )


def evaluate_hard_constraints(
    cv: CVMatchProfile,
    position: PositionMatchProfile,
    config: MatchingAlgorithmConfig,
) -> tuple[HardConstraintResult, ...]:
    """Evaluate each requirement and emit not_required for absent constraint types."""

    results: list[HardConstraintResult] = []
    by_type = {
        kind: tuple(item for item in position.hard_conditions if item.condition_type == kind)
        for kind in HARD_CONSTRAINT_TYPES
    }
    for kind in HARD_CONSTRAINT_TYPES:
        conditions = by_type[kind]
        if not conditions:
            results.append(_not_required(kind))  # type: ignore[arg-type]
            continue
        for condition in sorted(conditions, key=lambda item: item.condition_id):
            if kind == "education":
                results.append(_education(condition, cv, config))
            elif kind == "experience":
                results.append(_experience(condition, cv))
            elif kind == "certificate":
                results.append(_collection_constraint(condition, cv.certificates, "name"))
            elif kind == "language":
                results.append(_language(condition, cv, config))
            elif kind == "location":
                results.append(_feature_constraint(condition, cv, "location"))
            else:
                results.append(_feature_constraint(condition, cv, "availability"))
    return tuple(results)


def _capability_evidence(
    cv: CVMatchProfile,
    capability: CapabilityProfile,
) -> tuple[Evidence, ...]:
    links = tuple(
        item
        for item in cv.capability_evidence_links
        if item.link_id in capability.evidence_link_ids
    )
    return _evidence(links)


def _is_controlled_transfer(
    cv: CVMatchProfile,
    capability: CapabilityProfile,
) -> bool:
    return any(
        "controlled_skill_transfer" in item.support_signals
        for item in cv.capability_evidence_links
        if item.link_id in capability.evidence_link_ids
    )


_OWNERSHIP_ORDER = {
    "unknown": 0,
    "declared": 0,
    "used": 1,
    "participated": 1,
    "implemented": 2,
    "owned": 3,
    "designed": 4,
    "led": 5,
}
_OWNERSHIP_PATTERNS = (
    ("led", re.compile(r"主导|带领|\blead\b|负责人|\bowner\b", re.IGNORECASE)),
    (
        "designed",
        re.compile(
            r"设计|架构|\barchitect(?:ed|ure)?\b|\bdesign(?:ed)?\b",
            re.IGNORECASE,
        ),
    ),
    ("owned", re.compile(r"独立|自主|负责核心|\bown(?:ed)?\b", re.IGNORECASE)),
    (
        "implemented",
        re.compile(
            r"实现|开发|落地|构建|\bimplement(?:ed)?\b|\bdevelop(?:ed)?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "participated",
        re.compile(
            r"参与|协助|支持|\bparticipat(?:e|ed)\b|\bassist(?:ed)?\b",
            re.IGNORECASE,
        ),
    ),
    ("used", re.compile(r"使用|熟悉|掌握|\bused?\b|\bfamiliar\b", re.IGNORECASE)),
)


def _ownership_from_text(values: tuple[str, ...]) -> str | None:
    joined = " ".join(value for value in values if value)
    return next(
        (
            level
            for level, pattern in _OWNERSHIP_PATTERNS
            if pattern.search(joined)
        ),
        None,
    )


def _candidate_ownership(
    cv: CVMatchProfile,
    capability: CapabilityProfile,
) -> str | None:
    links = tuple(
        item
        for item in cv.capability_evidence_links
        if item.link_id in capability.evidence_link_ids
    )
    signaled = tuple(
        signal.split(":", 1)[1]
        for link in links
        for signal in link.support_signals
        if signal.startswith("ownership:") and signal.split(":", 1)[1] in _OWNERSHIP_ORDER
    )
    if signaled:
        return max(signaled, key=_OWNERSHIP_ORDER.__getitem__)
    inferred = _ownership_from_text(
        tuple(item.quote for link in links for item in link.evidence_refs)
    )
    if inferred:
        return inferred
    if links and capability.verification_status not in {"not_observed", "unresolved"}:
        return "used"
    return "declared" if capability.declared_feature_ids else None


def _required_ownership(requirement: PositionSkillRequirement) -> str | None:
    return _ownership_from_text(tuple(item.quote for item in requirement.evidence_refs))


def _is_semantic_text_evidence(
    cv: CVMatchProfile,
    capability: CapabilityProfile,
) -> bool:
    return any(
        "semantic_text_evidence" in item.support_signals
        for item in cv.capability_evidence_links
        if item.link_id in capability.evidence_link_ids
    )


def _ownership_satisfied(
    candidate: str | None,
    required: str | None,
) -> bool | None:
    if not required:
        return None
    if not candidate:
        return False
    current = _OWNERSHIP_ORDER.get(candidate.casefold())
    target = _OWNERSHIP_ORDER.get(required.casefold())
    if current is None or target is None:
        return None
    return current >= target


def _skill_result(
    requirement: PositionSkillRequirement,
    importance: Literal["required", "bonus"],
    *,
    status: str,
    reason: str,
    confidence: float,
    capability: CapabilityProfile | None = None,
    candidate_evidence: tuple[Evidence, ...] = (),
    exact_candidate: bool = False,
    match_type: str | None = None,
    relation_confidence: float | None = None,
    candidate_ownership: str | None = None,
    required_ownership: str | None = None,
    skill_present: bool = False,
    proficiency_satisfied: bool | None = None,
    ownership_satisfied: bool | None = None,
    evidence_sufficient: bool = False,
    semantic_evidence_link_ids: tuple[str, ...] = (),
) -> SkillResult:
    has_exact_candidate = capability is not None or exact_candidate
    return SkillResult(
        requirement_id=(
            requirement.requirement_id
            or f"{importance}:{requirement.skill_id or requirement.canonical_name}"
        ),
        skill_id=requirement.skill_id,
        skill_name=requirement.canonical_name,
        importance_level=importance,
        requirement_weight=requirement.importance,
        required_level=requirement.required_level,
        candidate_declared_level=(capability.declared_level if capability else None),
        candidate_demonstrated_level=(
            capability.demonstrated_level if capability else None
        ),
        verification_status=(capability.verification_status if capability else None),
        match_status=status,
        position_evidence=requirement.evidence_refs,
        candidate_evidence=candidate_evidence,
        reason_code=reason,
        confidence=confidence,
        match_type=match_type or ("exact" if has_exact_candidate else "none"),
        relation_type=(match_type if match_type in {"transferable"} else None),
        relation_confidence=relation_confidence,
        transferability_score=(
            relation_confidence
            if match_type == "transferable" and relation_confidence is not None
            else 1.0
            if has_exact_candidate and status == "matched"
            else 0.0
        ),
        candidate_ownership=candidate_ownership,
        required_ownership=required_ownership,
        skill_present=skill_present,
        proficiency_satisfied=proficiency_satisfied,
        ownership_satisfied=ownership_satisfied,
        evidence_sufficient=evidence_sufficient,
        semantic_evidence_link_ids=semantic_evidence_link_ids,
    )


def _evaluate_skill(
    requirement: PositionSkillRequirement,
    importance: Literal["required", "bonus"],
    cv: CVMatchProfile,
    config: MatchingAlgorithmConfig,
) -> SkillResult:
    required_ownership = _required_ownership(requirement)
    if requirement.resolution_status != "resolved" or requirement.skill_id is None:
        return _skill_result(
            requirement, importance, status="unresolved",
            reason="REQUIREMENT_UNRESOLVED", confidence=0.0,
            required_ownership=required_ownership,
        )
    # 上游 CV 与岗位目录可能使用不同版本的技能 ID。只有规范名称完全一致时
    # 才允许跨版本对齐，避免因模糊词或别名把两个技能误判为同一能力。
    canonical_name = (requirement.canonical_name or "").strip().casefold()
    def same_skill(skill_id: str | None, name: str | None) -> bool:
        return skill_id == requirement.skill_id or (
            bool(canonical_name) and (name or "").strip().casefold() == canonical_name
        )

    capabilities = tuple(
        item
        for item in cv.capability_profiles
        if same_skill(item.skill_id, item.canonical_name)
    )
    declared = tuple(
        item for item in cv.skills if same_skill(item.skill_id, item.canonical_name)
    )
    if not capabilities:
        if declared:
            item = sorted(declared, key=lambda value: value.aggregation_key)[0]
            if item.resolution_status != "resolved" or item.verification_status == "unresolved":
                return _skill_result(
                    requirement, importance, status="unresolved",
                    reason="CANDIDATE_SKILL_UNRESOLVED", confidence=0.0,
                    candidate_evidence=item.evidence_refs,
                    exact_candidate=True,
                    required_ownership=required_ownership,
                    skill_present=True,
                    proficiency_satisfied=(
                        False if requirement.required_level else None
                    ),
                    ownership_satisfied=_ownership_satisfied(
                        "declared", required_ownership
                    ),
                )
            if item.declared_level is not None or item.verification_status == "not_observed":
                return _skill_result(
                    requirement, importance, status="declared_only",
                    reason="SKILL_DECLARED_WITHOUT_EVIDENCE",
                    confidence=item.normalization_confidence * 0.4,
                    candidate_evidence=item.evidence_refs,
                    exact_candidate=True,
                    candidate_ownership="declared",
                    required_ownership=required_ownership,
                    skill_present=True,
                    proficiency_satisfied=(
                        False if requirement.required_level else None
                    ),
                    ownership_satisfied=_ownership_satisfied(
                        "declared", required_ownership
                    ),
                )
            return _skill_result(
                requirement, importance, status="unknown", reason="CANDIDATE_SKILL_UNKNOWN",
                confidence=0.0, candidate_evidence=item.evidence_refs,
                exact_candidate=True,
                required_ownership=required_ownership,
                skill_present=True,
                proficiency_satisfied=(
                    False if requirement.required_level else None
                ),
                ownership_satisfied=_ownership_satisfied(
                    "declared", required_ownership
                ),
            )
        return _skill_result(
            requirement, importance, status="missing",
            reason="REQUIRED_SKILL_NOT_OBSERVED" if importance == "required"
            else "BONUS_SKILL_NOT_OBSERVED",
            confidence=1.0,
            required_ownership=required_ownership,
        )
    capability = sorted(
        capabilities,
        key=lambda item: (-item.support_confidence, item.profile_id),
    )[0]
    evidence = _capability_evidence(cv, capability)
    candidate_ownership = _candidate_ownership(cv, capability)
    semantic_link_ids = tuple(
        item.link_id
        for item in cv.capability_evidence_links
        if item.link_id in capability.evidence_link_ids
        and "semantic_text_evidence" in item.support_signals
    )
    if capability.resolution_status != "resolved" or capability.verification_status == "unresolved":
        return _skill_result(
            requirement, importance, status="unresolved",
            reason="CANDIDATE_SKILL_UNRESOLVED", confidence=0.0,
            capability=capability, candidate_evidence=evidence,
            candidate_ownership=candidate_ownership,
            required_ownership=required_ownership,
            skill_present=True,
            proficiency_satisfied=(
                False if requirement.required_level else None
            ),
            ownership_satisfied=_ownership_satisfied(
                candidate_ownership, required_ownership
            ),
        )
    if capability.verification_status == "not_observed":
        if capability.declared_level is not None or capability.declared_feature_ids:
            return _skill_result(
                requirement, importance, status="declared_only",
                reason="SKILL_DECLARED_WITHOUT_EVIDENCE",
                confidence=capability.support_confidence,
                capability=capability, candidate_evidence=evidence,
                candidate_ownership=candidate_ownership,
                required_ownership=required_ownership,
                skill_present=True,
                proficiency_satisfied=(
                    False if requirement.required_level else None
                ),
                ownership_satisfied=_ownership_satisfied(
                    candidate_ownership, required_ownership
                ),
            )
        return _skill_result(
            requirement, importance, status="unknown", reason="CANDIDATE_SKILL_UNKNOWN",
            confidence=0.0, capability=capability, candidate_evidence=evidence,
            candidate_ownership=candidate_ownership,
            required_ownership=required_ownership,
            skill_present=True,
            proficiency_satisfied=(
                False if requirement.required_level else None
            ),
            ownership_satisfied=_ownership_satisfied(
                candidate_ownership, required_ownership
            ),
        )
    confidence = capability.support_confidence
    if capability.verification_status == "partially_supported":
        confidence *= config.partially_supported_confidence_factor
    if not evidence or confidence <= 0:
        return _skill_result(
            requirement,
            importance,
            status="unknown",
            reason="CANDIDATE_EVIDENCE_UNKNOWN",
            confidence=confidence,
            capability=capability,
            candidate_evidence=evidence,
            candidate_ownership=candidate_ownership,
            required_ownership=required_ownership,
            skill_present=True,
            proficiency_satisfied=(
                False if requirement.required_level else None
            ),
            ownership_satisfied=_ownership_satisfied(
                candidate_ownership, required_ownership
            ),
        )
    ownership_satisfied = _ownership_satisfied(
        candidate_ownership, required_ownership
    )
    if _is_semantic_text_evidence(cv, capability):
        return _skill_result(
            requirement,
            importance,
            status="matched",
            reason="SEMANTIC_TEXT_EVIDENCE_PRESENT",
            confidence=confidence,
            capability=capability,
            candidate_evidence=evidence,
            match_type="semantic_text",
            candidate_ownership=candidate_ownership,
            required_ownership=required_ownership,
            skill_present=True,
            proficiency_satisfied=(
                False if requirement.required_level else None
            ),
            ownership_satisfied=None,
            evidence_sufficient=True,
            semantic_evidence_link_ids=semantic_link_ids,
        )
    if _is_controlled_transfer(cv, capability):
        return _skill_result(
            requirement,
            importance,
            status="partial",
            reason="CONTROLLED_TRANSFER_PARTIAL_MATCH",
            confidence=confidence,
            capability=capability,
            candidate_evidence=evidence,
            match_type="transferable",
            relation_confidence=confidence,
            candidate_ownership=candidate_ownership,
            required_ownership=required_ownership,
            skill_present=True,
            proficiency_satisfied=(
                False if requirement.required_level else None
            ),
            ownership_satisfied=ownership_satisfied,
            evidence_sufficient=True,
        )
    if capability.demonstrated_level == "unknown":
        return _skill_result(
            requirement, importance, status="unknown", reason="CANDIDATE_LEVEL_UNKNOWN",
            confidence=confidence, capability=capability, candidate_evidence=evidence,
            candidate_ownership=candidate_ownership,
            required_ownership=required_ownership,
            skill_present=True,
            proficiency_satisfied=(
                False if requirement.required_level else None
            ),
            ownership_satisfied=ownership_satisfied,
            evidence_sufficient=True,
        )
    if requirement.required_level is None:
        status = "matched" if confidence > 0 else "unknown"
        return _skill_result(
            requirement, importance, status=status,
            reason="EXACT_SKILL_EVIDENCE_PRESENT" if status == "matched"
            else "CANDIDATE_SKILL_UNKNOWN",
            confidence=confidence, capability=capability, candidate_evidence=evidence,
            candidate_ownership=candidate_ownership,
            required_ownership=required_ownership,
            skill_present=True,
            proficiency_satisfied=None,
            ownership_satisfied=ownership_satisfied,
            evidence_sufficient=True,
        )
    required_rank = _rank(requirement.required_level, config.capability_levels)
    candidate_rank = _rank(capability.demonstrated_level, config.capability_levels)
    if required_rank is None or candidate_rank is None:
        return _skill_result(
            requirement, importance, status="unknown", reason="SKILL_LEVEL_UNKNOWN",
            confidence=0.0, capability=capability, candidate_evidence=evidence,
            candidate_ownership=candidate_ownership,
            required_ownership=required_ownership,
            skill_present=True,
            proficiency_satisfied=False,
            ownership_satisfied=ownership_satisfied,
            evidence_sufficient=True,
        )
    met = candidate_rank >= required_rank
    return _skill_result(
        requirement,
        importance,
        status="matched",
        reason="EXACT_SKILL_LEVEL_MET" if met else "EXACT_SKILL_PRESENT_LEVEL_BELOW",
        confidence=confidence,
        capability=capability,
        candidate_evidence=evidence,
        candidate_ownership=candidate_ownership,
        required_ownership=required_ownership,
        skill_present=True,
        proficiency_satisfied=met,
        ownership_satisfied=ownership_satisfied,
        evidence_sufficient=True,
    )


def evaluate_skills(
    cv: CVMatchProfile,
    position: PositionMatchProfile,
    config: MatchingAlgorithmConfig,
) -> tuple[SkillResult, ...]:
    required = tuple(
        _evaluate_skill(item, "required", cv, config)
        for item in sorted(
            position.required_skills,
            key=lambda value: (value.skill_id or "", value.canonical_name or ""),
        )
    )
    bonus = tuple(
        _evaluate_skill(item, "bonus", cv, config)
        for item in sorted(
            position.preferred_skills,
            key=lambda value: (value.skill_id or "", value.canonical_name or ""),
        )
    )
    return required + bonus


def prepare_effective_position(
    cv: CVMatchProfile,
    position: PositionMatchProfile,
    config: MatchingAlgorithmConfig,
    relations: tuple[SkillRelation, ...] = (),
    *,
    target_type: str = "standard_position",
) -> tuple[PositionMatchProfile, SpecialtyRouteSelection | None]:
    """Select a source-JD route before any formal downstream calculation."""

    if target_type != "standard_position":
        return position, None
    preliminary = apply_skill_relations(
        evaluate_skills(cv, position, config),
        cv,
        relations,
        config.capability_levels,
        config.relations,
    )
    selection = select_specialty_route(position, preliminary)
    return apply_effective_required_set(position, selection), selection


def _coverage(results: tuple[SkillResult, ...], importance: str) -> float | None:
    evaluable = tuple(
        item
        for item in results
        if item.importance_level == importance
        and item.match_status not in {"unknown", "unresolved"}
    )
    if not evaluable:
        return None
    return sum(
        item.match_status == "matched" and item.match_type == "exact"
        for item in evaluable
    ) / len(evaluable)


def _hard_pass_rate(results: tuple[HardConstraintResult, ...]) -> float | None:
    evaluable = tuple(item for item in results if item.status in {"pass", "partial", "fail"})
    if not evaluable:
        return None
    return sum(item.status == "pass" for item in evaluable) / len(evaluable)


_SECONDARY_HARD_TYPES = frozenset(
    {"certificate", "language", "location", "availability"}
)


def evaluate_information_sufficiency(
    cv: CVMatchProfile,
    position: PositionMatchProfile,
    evaluation: MatchEvaluation,
) -> tuple[str, tuple[str, ...]]:
    """Classify uncertainty into blocking / material / minor.

    blocking: a core Hard Gate or required skill cannot be decided -> the
    recommendation is withheld.  material: a recommendation is still allowed
    but confidence is discounted.  minor: recorded as a hint only.
    """

    blocking: list[str] = []
    material: list[str] = []
    minor: list[str] = []
    for item in cv.unresolved_items:
        field = str(item.raw_value).casefold()
        # An unresolved source field only matters through the actual
        # evaluation outcome (HARD_CONDITION_UNCERTAIN /
        # REQUIRED_SKILL_UNCERTAIN).  By itself it is material, never blocking.
        material.append(f"CV_UNRESOLVED_FIELD:{field}")
    if cv.review_status in {"pending", "rejected"}:
        blocking.append(f"CV_REVIEW_STATUS:{cv.review_status}")
    elif cv.review_status == "needs_human_review":
        minor.append("CV_REVIEW_NEEDS_HUMAN")
    if position.unresolved_items:
        material.append("POSITION_UNRESOLVED_ITEMS")
    if position.quality_context.status not in {"trusted", "not_applicable"}:
        blocking.append(f"POSITION_QUALITY:{position.quality_context.status}")
    if any(
        item.status in {"unknown", "unresolved"}
        and item.constraint_type not in _SECONDARY_HARD_TYPES
        for item in evaluation.hard_constraint_results
    ):
        blocking.append("HARD_CONDITION_UNCERTAIN")
    if any(
        item.status in {"unknown", "unresolved"}
        and item.constraint_type in _SECONDARY_HARD_TYPES
        for item in evaluation.hard_constraint_results
    ):
        material.append("SECONDARY_HARD_CONDITION_UNCERTAIN")
    if any(
        item.importance_level == "required"
        and item.match_status in {"unknown", "unresolved"}
        for item in evaluation.skill_results
    ):
        presence_known = any(
            item.importance_level == "required"
            and item.match_status in {"unknown", "unresolved"}
            and item.skill_present
            and item.evidence_sufficient
            for item in evaluation.skill_results
        )
        if presence_known:
            material.append("SKILL_PROFICIENCY_UNCERTAIN")
        else:
            blocking.append("REQUIRED_SKILL_UNCERTAIN")
    if any(
        item.match_status in {"unknown", "unresolved"}
        for item in (
            evaluation.responsibility_results
            + evaluation.project_results
            + evaluation.scenario_results
        )
    ):
        material.append("CONTEXT_UNCERTAIN")
    if evaluation.unresolved_count > 0:
        material.append("UNRESOLVED_RESULTS")
    if any(
        item.status == "fail" for item in evaluation.hard_constraint_results
    ):
        # A definite Hard Gate failure already determines the recommendation;
        # co-existing unknowns cannot change that conclusion.
        blocking.clear()
    if blocking:
        level = "blocking"
    elif material:
        level = "material"
    elif minor:
        level = "minor"
    else:
        level = "sufficient"
    return level, tuple(dict.fromkeys((*blocking, *material, *minor)))


def _matching_input_coverage(
    cv: CVMatchProfile,
    position: PositionMatchProfile,
    hard: tuple[HardConstraintResult, ...],
) -> dict[str, object]:
    candidate_responsibilities = bool(
        cv.work_experiences
        or cv.projects
        or any(
            item.feature_type in {"task", "experience"}
            for item in cv.match_features
        )
    )
    candidate_location = any(
        item.feature_type == "location" for item in cv.match_features
    )
    candidate_availability = any(
        item.feature_type == "availability" for item in cv.match_features
    )

    def coverage(
        condition_type: str,
        candidate_available: bool,
    ) -> dict[str, object]:
        condition_count = sum(
            1
            for item in position.hard_conditions
            if item.condition_type == condition_type
        )
        condition_available = condition_count > 0
        return {
            "count": condition_count,
            "available": condition_available and candidate_available,
            "condition_available": condition_available,
            "candidate_available": candidate_available,
        }

    return {
        "required_skills": {
            "count": len(position.required_skills),
            "available": bool(position.required_skills) and bool(cv.skills),
            "condition_available": bool(position.required_skills),
            "candidate_available": bool(cv.skills),
        },
        "responsibilities": {
            "count": len(position.core_responsibilities),
            "available": bool(position.responsibility_requirements)
            and candidate_responsibilities,
            "condition_available": bool(position.core_responsibilities),
            "candidate_available": candidate_responsibilities,
        },
        **{
            condition_type: coverage(condition_type, candidate_available)
            for condition_type, candidate_available in {
                "education": bool(cv.education),
                "experience": bool(cv.work_experiences),
                "certificate": bool(cv.certificates),
                "language": bool(cv.languages),
                "location": candidate_location,
                "availability": candidate_availability,
            }.items()
        },
    }


def build_match_evaluation(
    cv: CVMatchProfile,
    position: PositionMatchProfile,
    config: MatchingAlgorithmConfig,
    relations: tuple[SkillRelation, ...] = (),
    *,
    target_type: str = "standard_position",
) -> MatchEvaluation:
    effective_position, route_selection = prepare_effective_position(
        cv,
        position,
        config,
        relations,
        target_type=target_type,
    )
    hard = evaluate_hard_constraints(cv, effective_position, config)
    exact_skills = evaluate_skills(cv, effective_position, config)
    skills = apply_skill_relations(
        exact_skills,
        cv,
        relations,
        config.capability_levels,
        config.relations,
    )
    responsibilities = evaluate_responsibilities(
        cv, effective_position, config.context
    )
    context_enabled = config.context.context_matching_enabled
    projects = (
        evaluate_projects(
            cv,
            effective_position,
            config.context,
            canonical_skill_identity_enabled=(target_type == "standard_position"),
            include_work_experiences=(target_type == "standard_position"),
        )
        if context_enabled
        else ()
    )
    scenarios = (
        evaluate_scenarios(cv, effective_position, config.context)
        if context_enabled
        else ()
    )
    evaluation_id = "eval_" + "|".join(
        (
            cv.profile_id,
            cv.profile_version,
            effective_position.profile_id,
            effective_position.profile_version,
            config.algorithm_version,
        )
    )
    skill_uncertainty_results = (
        tuple(item for item in skills if item.importance_level == "required")
        if route_selection is not None
        else skills
    )
    unresolved = sum(item.status == "unresolved" for item in hard) + sum(
        item.match_status == "unresolved" for item in skill_uncertainty_results
    )
    unknown = sum(item.status == "unknown" for item in hard) + sum(
        item.match_status == "unknown" for item in skill_uncertainty_results
    )
    unresolved += sum(
        item.match_status == "unresolved"
        for item in responsibilities + projects + scenarios
    )
    unresolved += len(cv.unresolved_items) + len(position.unresolved_items)
    unknown += sum(
        item.match_status == "unknown"
        for item in responsibilities + projects + scenarios
    )
    summary = EvaluationSummary(
        hard_constraint_pass_count=sum(item.status == "pass" for item in hard),
        hard_constraint_fail_count=sum(item.status == "fail" for item in hard),
        required_skill_matched_count=sum(
            item.importance_level == "required" and item.match_status == "matched"
            for item in skills
        ),
        required_skill_missing_count=sum(
            item.importance_level == "required" and item.match_status == "missing"
            for item in skills
        ),
        bonus_skill_matched_count=sum(
            item.importance_level == "bonus" and item.match_status == "matched"
            for item in skills
        ),
        bonus_skill_missing_count=sum(
            item.importance_level == "bonus" and item.match_status == "missing"
            for item in skills
        ),
    )
    evaluation = MatchEvaluation(
        evaluation_id=evaluation_id,
        cv_profile_id=cv.profile_id,
        cv_profile_version=cv.profile_version,
        position_profile_id=position.profile_id,
        position_profile_version=position.profile_version,
        algorithm_version=config.algorithm_version,
        evaluation_status="completed",
        hard_constraint_results=hard,
        skill_results=skills,
        responsibility_results=responsibilities,
        project_results=projects,
        scenario_results=scenarios,
        required_skill_coverage=_coverage(skills, "required"),
        bonus_skill_coverage=_coverage(skills, "bonus"),
        hard_constraint_pass_rate=_hard_pass_rate(hard),
        required_transferable_coverage=transferable_coverage(skills, "required"),
        bonus_transferable_coverage=transferable_coverage(skills, "bonus"),
        responsibility_coverage=context_coverage(
            responsibilities, config.context.partial_coverage_weight
        ),
        project_coverage=context_coverage(
            projects, config.context.partial_coverage_weight
        ),
        scenario_coverage=context_coverage(
            scenarios, config.context.partial_coverage_weight
        ),
        input_coverage=_matching_input_coverage(
            cv,
            effective_position,
            hard,
        ),
        unresolved_count=unresolved,
        unknown_count=unknown,
        summary=summary,
    )
    group_results = evaluate_requirement_graph(
        effective_position,
        evaluation,
        selected_route_id=route_selection.route_id if route_selection else None,
    )
    evaluation = evaluation.model_copy(
        update={"requirement_group_results": group_results}
    )
    sufficiency_level, sufficiency_reasons = evaluate_information_sufficiency(
        cv, effective_position, evaluation
    )
    evaluation = evaluation.model_copy(
        update={
            "information_sufficient": sufficiency_level != "blocking",
            "information_sufficiency_level": sufficiency_level,
            "information_sufficiency_reasons": sufficiency_reasons,
        }
    )
    final_result = score_match_evaluation(evaluation, cv, effective_position)
    return evaluation.model_copy(update={"final_match_result": final_result})
