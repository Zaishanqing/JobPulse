"""Pure deterministic responsibility, project, and scenario matching."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from app.domain.evaluation import ProjectResult, ResponsibilityResult, ScenarioResult
from app.domain.profiles import (
    CVMatchProfile,
    Evidence,
    ExperienceFeature,
    MatchFeature,
    PositionMatchProfile,
    PositionResponsibilityRequirement,
)

_TOKEN_PATTERN = re.compile(r"[a-z0-9+#.]+|[\u4e00-\u9fff]+")
_ACTION_TERMS = frozenset(
    {
        "build",
        "develop",
        "design",
        "implement",
        "maintain",
        "optimize",
        "lead",
        "manage",
        "开发",
        "设计",
        "实现",
        "构建",
        "维护",
        "优化",
        "主导",
        "管理",
        "负责",
    }
)
_STOP_WORDS = frozenset(
    {"a", "an", "and", "for", "of", "the", "to", "with", "与", "和", "的", "及"}
)
# These verbs/nouns occur in almost every JD and should not create a semantic
# responsibility match by themselves.  They are retained for action checks,
# but excluded from the fallback overlap score.
_GENERIC_CONTEXT_TOKENS = frozenset(
    {"负责", "完成", "参与", "支持", "进行", "相关", "工作", "项目", "系统", "业务", "服务", "开发"}
)
_SEMANTIC_CONCEPT_ALIASES = {
    "模型训练": "concept:model_training",
    "训练模型": "concept:model_training",
    "模型微调": "concept:model_training",
    "model training": "concept:model_training",
    "train models": "concept:model_training",
    "fine tuning": "concept:model_training",
    "fine-tuning": "concept:model_training",
    "服务化": "concept:deployment",
    "模型部署": "concept:deployment",
    "deployment": "concept:deployment",
    "serving": "concept:deployment",
    "数据分析": "concept:data_analysis",
    "分析数据": "concept:data_analysis",
    "data analysis": "concept:data_analysis",
    "系统设计": "concept:system_design",
    "架构设计": "concept:system_design",
    "system design": "concept:system_design",
    "architecture design": "concept:system_design",
    "性能优化": "concept:optimization",
    "优化性能": "concept:optimization",
    "optimization": "concept:optimization",
    "项目管理": "concept:project_management",
    "project management": "concept:project_management",
    "智能体": "concept:agent_development",
    "多智能体": "concept:agent_development",
    "agent": "concept:agent_development",
    "langgraph": "concept:agent_development",
    "技术架构": "concept:system_design",
    "指标分析": "concept:data_analysis",
    "用户数据分析": "concept:data_analysis",
    "推理服务": "concept:deployment",
}


@dataclass(frozen=True)
class ContextMatchingConfig:
    exact_confidence: float = 1.0
    action_keyword_confidence: float = 0.85
    keyword_confidence: float = 0.65
    normalized_name_confidence: float = 0.9
    partial_coverage_weight: float = 0.5
    # A single generic word (for example ``开发`` or ``服务``) is not enough
    # evidence for a responsibility match.  Require the candidate to cover a
    # meaningful fraction of the requirement tokens before emitting ``partial``.
    minimum_partial_coverage: float = 0.6
    # Keep the legacy best candidate fields, but expose additional evidence
    # items for relation-level evaluation and explainability.  This does not
    # change the status or score of a responsibility result.
    max_candidate_evidence: int = 8
    semantic_concept_alias_enabled: bool = True
    responsibility_candidate_mode: Literal[
        "combined", "structured_sentence", "match_feature"
    ] = "combined"
    responsibility_matching_enabled: bool = True
    context_matching_enabled: bool = True


@dataclass(frozen=True)
class _CandidateText:
    experience_id: str
    text: str
    evidence: tuple[Evidence, ...]
    source: str
    resolution_status: str = "resolved"


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(_TOKEN_PATTERN.findall(normalized))


def tokenize(value: str) -> frozenset[str]:
    tokens: set[str] = set()
    for part in _TOKEN_PATTERN.findall(unicodedata.normalize("NFKC", value).casefold()):
        if part in _STOP_WORDS:
            continue
        tokens.add(part)
        if re.fullmatch(r"[\u4e00-\u9fff]+", part) and len(part) > 1:
            tokens.update(part[index : index + 2] for index in range(len(part) - 1))
    return frozenset(tokens)


def _concept_ids(value: str, *, enabled: bool = True) -> frozenset[str]:
    if not enabled:
        return frozenset()
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return frozenset(
        concept
        for alias, concept in _SEMANTIC_CONCEPT_ALIASES.items()
        if alias in normalized
    )


def _split_candidate_text(value: str) -> tuple[str, ...]:
    """Split OCR paragraphs into evidence-sized action/object/result units."""
    parts = tuple(
        part.strip(" \t\r\n-:：,，")
        for part in re.split(r"[。；;！？!?\n]+", value)
        if part.strip(" \t\r\n-:：,，")
    )
    return parts or ((value.strip(),) if value.strip() else ())


def verify_semantic_candidate(
    requirement: str,
    candidate_text: str,
    *,
    minimum_coverage: float = 0.25,
    minimum_specific_tokens: int = 2,
    semantic_concept_alias_enabled: bool = True,
) -> bool:
    """Reject generic or weak semantic hits before exposing them as evidence."""
    requirement_tokens = tokenize(requirement) - _GENERIC_CONTEXT_TOKENS
    candidate_tokens = tokenize(candidate_text) - _GENERIC_CONTEXT_TOKENS
    if not requirement_tokens or not candidate_tokens:
        return False
    normalized_requirement = unicodedata.normalize("NFKC", requirement).casefold()
    normalized_candidate = unicodedata.normalize("NFKC", candidate_text).casefold()
    if semantic_concept_alias_enabled:
        requirement_concepts = {
            concept
            for alias, concept in _SEMANTIC_CONCEPT_ALIASES.items()
            if alias in normalized_requirement
        }
        candidate_concepts = {
            concept
            for alias, concept in _SEMANTIC_CONCEPT_ALIASES.items()
            if alias in normalized_candidate
        }
    else:
        requirement_concepts = set()
        candidate_concepts = set()
    concept_overlap = requirement_concepts.intersection(candidate_concepts)
    overlap = requirement_tokens.intersection(candidate_tokens)
    coverage = len(overlap) / len(requirement_tokens)
    return (
        len(overlap) >= minimum_specific_tokens and coverage >= minimum_coverage
    ) or bool(concept_overlap) and coverage >= minimum_coverage


def _dedupe_evidence(groups: tuple[tuple[Evidence, ...], ...]) -> tuple[Evidence, ...]:
    values = {
        (
            item.source_id,
            item.quote,
            item.start,
            item.end,
            item.alignment,
            item.occurrence_index,
        ): item
        for group in groups
        for item in group
    }
    return tuple(values[key] for key in sorted(values, key=str))


def _matching_evidence(text: str, evidence: tuple[Evidence, ...]) -> tuple[Evidence, ...]:
    tokens = tokenize(text)
    matched = tuple(
        item
        for item in evidence
        if normalize_text(text) in normalize_text(item.quote)
        or (
            bool(tokens)
            and len(tokens.intersection(tokenize(item.quote))) / len(tokens) >= 0.5
        )
    )
    return matched


def _position_evidence(
    requirement: str,
    evidence: tuple[Evidence, ...],
) -> tuple[Evidence, ...]:
    return _matching_evidence(requirement, evidence)


def _is_name_only(feature: MatchFeature) -> bool:
    scope = normalize_text(feature.source_scope).replace(" ", "_")
    return scope.endswith(":name") or scope.endswith("_name") or ":name:" in scope


def _experience_key(value: str | None) -> tuple[str, int] | None:
    """Extract ``(kind, ordinal)`` from feature/experience identifiers.

    The native extraction adapter emits task features with scoped ids such as
    ``work_experience:work_001`` and experience ids such as
    ``cv_000001:work_experience:1``.  Both encodings must be resolved to the
    same logical experience before candidate evidence can be bound.
    """
    if not value:
        return None
    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    if re.search(r"project|proj", normalized):
        kind = "project"
    elif "work" in normalized:
        kind = "work"
    else:
        return None
    digits = re.findall(r"\d+", normalized)
    return (kind, int(digits[-1])) if digits else None


def _same_experience(feature: MatchFeature, experience: ExperienceFeature) -> bool:
    """Bind features across the accepted upstream id encodings.

    Extractors have historically emitted either the canonical experience id or
    a scoped id such as ``work_experiences/<id>``.  Strict equality silently
    discarded otherwise valid task evidence.
    """
    source_key = _experience_key(feature.source_scope) or _experience_key(
        feature.source_object_id
    )
    target_key = _experience_key(experience.experience_id)
    if source_key is not None and target_key is not None and source_key == target_key:
        return True
    source = normalize_text(feature.source_object_id).replace(" ", "")
    target = normalize_text(experience.experience_id).replace(" ", "")
    if source == target or source.endswith(target) or target.endswith(source):
        return True
    source_tail = re.split(r"[/:#]", source)[-1]
    target_tail = re.split(r"[/:#]", target)[-1]
    return bool(source_tail) and source_tail == target_tail


def _linked_features(
    cv: CVMatchProfile,
    experience: ExperienceFeature,
) -> tuple[MatchFeature, ...]:
    return tuple(
        item
        for item in cv.match_features
        if _same_experience(item, experience)
        and item.feature_type in {"task", "experience"}
        and not _is_name_only(item)
    )


def _candidate_texts(
    cv: CVMatchProfile,
    config: ContextMatchingConfig,
) -> tuple[_CandidateText, ...]:
    values: list[_CandidateText] = []
    seen_keys: set[tuple[str, str]] = set()

    def append(candidate: _CandidateText) -> None:
        key = (candidate.experience_id, candidate.text)
        if key in seen_keys:
            return
        seen_keys.add(key)
        values.append(candidate)

    use_structured = config.responsibility_candidate_mode in {
        "combined",
        "structured_sentence",
    }
    use_match_features = config.responsibility_candidate_mode in {
        "combined",
        "match_feature",
    }
    for experience in cv.work_experiences + cv.projects:
        # Responsibilities are the primary relation source.  Business
        # scenarios are also first-class CV evidence in the contract and are
        # commonly where an extracted project describes its actual work.
        # Previously those texts were silently omitted when responsibilities
        # extraction was incomplete.
        for raw_text in (
            tuple(getattr(experience, "responsibilities", ()))
            + tuple(getattr(experience, "business_scenarios", ()))
            if use_structured
            else ()
        ):
            # Keep the original paragraph as a fallback context while also
            # exposing sentence-sized candidates for precise evidence links.
            split_texts = _split_candidate_text(raw_text)
            paragraph_fallback = (
                (raw_text.strip(),)
                if config.responsibility_candidate_mode == "combined"
                and len(split_texts) > 1
                else ()
            )
            for text in paragraph_fallback + split_texts:
                append(
                    _CandidateText(
                        experience.experience_id,
                        text,
                        # Every sentence retains the original source evidence;
                        # splitting must never weaken relation grounding.
                        _matching_evidence(text, experience.evidence_refs) or experience.evidence_refs,
                        "responsibility",
                        "resolved",
                    )
                )
        for feature in (
            _linked_features(cv, experience) if use_match_features else ()
        ):
            append(
                _CandidateText(
                    experience.experience_id,
                    feature.raw_text,
                    feature.evidence_refs,
                    "match_feature",
                    feature.resolution_status,
                )
            )
    return tuple(values)


def _text_match(
    required: str,
    candidate: str,
    config: ContextMatchingConfig,
) -> tuple[str, tuple[str, ...], float]:
    required_normalized = normalize_text(required)
    candidate_normalized = normalize_text(candidate)
    required_tokens = tokenize(required)
    candidate_tokens = tokenize(candidate)
    # Do not use raw substring matching here: ``Java`` must not match
    # ``JavaScript`` and short Chinese fragments must not create a false exact
    # match.  Exact means token-complete coverage of the requirement.
    if required_normalized == candidate_normalized or (
        bool(required_tokens) and required_tokens.issubset(candidate_tokens)
    ):
        return "matched", ("normalized_text_exact",), config.exact_confidence
    common = required_tokens.intersection(candidate_tokens)
    shared_concepts = _concept_ids(
        required,
        enabled=config.semantic_concept_alias_enabled,
    ).intersection(
        _concept_ids(candidate, enabled=config.semantic_concept_alias_enabled)
    )
    concrete_common = common - _GENERIC_CONTEXT_TOKENS - _ACTION_TERMS
    action_common = common.intersection(_ACTION_TERMS)
    concept_coverage = len(common) / max(1, len(required_tokens))
    if shared_concepts and concept_coverage >= 0.25 and (concrete_common or action_common):
        # Small, auditable phrase rules for recurring cross-CV paraphrases.
        # They require the same multi-token concept on both sides, so a broad
        # verb such as “开发” cannot create a relation by itself.
        return "partial", ("high_precision_concept_alias",), config.action_keyword_confidence
    if not common:
        return "not_observed", (), 0.0
    coverage = len(common) / len(required_tokens)
    if coverage < config.minimum_partial_coverage:
        meaningful_required = required_tokens - _GENERIC_CONTEXT_TOKENS
        meaningful_common = common.intersection(meaningful_required)
        meaningful_coverage = len(meaningful_common) / max(1, len(meaningful_required))
        # Deterministic matching remains strict above. This bounded fallback
        # handles paraphrased Chinese/English responsibility text without
        # allowing a single generic verb to match. It is intentionally
        # evidence-bound and emits a lower-confidence partial result.
        if len(meaningful_common) >= 2 and meaningful_coverage >= 0.25:
            return "partial", ("semantic_token_overlap",), 0.55
        return "not_observed", (), 0.0
    required_actions = required_tokens.intersection(_ACTION_TERMS)
    candidate_actions = candidate_tokens.intersection(_ACTION_TERMS)
    non_action = common - _ACTION_TERMS
    if required_actions.intersection(candidate_actions) and non_action:
        return (
            "partial",
            ("action_overlap", "keyword_overlap"),
            config.action_keyword_confidence,
        )
    return "partial", ("keyword_overlap",), config.keyword_confidence


def _unresolved_requirement(
    cv: CVMatchProfile | PositionMatchProfile,
    item_type: str,
    value: str,
) -> bool:
    normalized = normalize_text(value)
    return any(
        item.item_type == item_type and normalize_text(item.raw_value) == normalized
        for item in cv.unresolved_items
    )


def _disabled_responsibility_results(
    position: PositionMatchProfile,
) -> tuple[ResponsibilityResult, ...]:
    """Emit fixed not-observed results while preserving the JD denominator."""
    results: list[ResponsibilityResult] = []
    for requirement in _position_responsibilities(position):
        results.append(
            ResponsibilityResult(
                requirement_id=requirement.requirement_id,
                position_requirement=requirement.text,
                candidate_experience_id=None,
                candidate_experience=None,
                match_status="not_observed",
                position_evidence=requirement.evidence_refs,
                candidate_evidence=(),
                reason_code="RESPONSIBILITY_MECHANISM_DISABLED",
                confidence=1.0,
                status_detail="not_observed",
            )
        )
    return tuple(results)


def evaluate_responsibilities(
    cv: CVMatchProfile,
    position: PositionMatchProfile,
    config: ContextMatchingConfig,
) -> tuple[ResponsibilityResult, ...]:
    if not config.responsibility_matching_enabled:
        return _disabled_responsibility_results(position)
    candidates = _candidate_texts(cv, config)
    results: list[ResponsibilityResult] = []
    for requirement in _position_responsibilities(position):
        requirement_id = requirement.requirement_id
        position_evidence = requirement.evidence_refs
        if _unresolved_requirement(cv, "responsibility", requirement.text) or (
            _unresolved_requirement(position, "responsibility", requirement.text)
        ):
            results.append(
                ResponsibilityResult(
                    requirement_id=requirement_id,
                    position_requirement=requirement.text,
                    candidate_experience_id=None,
                    candidate_experience=None,
                    match_status="unresolved",
                    position_evidence=position_evidence,
                    reason_code="RESPONSIBILITY_UNRESOLVED",
                    confidence=0.0,
                )
            )
            continue
        if not position_evidence:
            results.append(
                ResponsibilityResult(
                    requirement_id=requirement_id,
                    position_requirement=requirement.text,
                    candidate_experience_id=None,
                    candidate_experience=None,
                    match_status="unknown",
                    reason_code="POSITION_EVIDENCE_UNKNOWN",
                    confidence=0.0,
                )
            )
            continue
        if not candidates:
            results.append(
                ResponsibilityResult(
                    requirement_id=requirement_id,
                    position_requirement=requirement.text,
                    candidate_experience_id=None,
                    candidate_experience=None,
                    match_status="unknown",
                    position_evidence=position_evidence,
                    reason_code="CANDIDATE_RESPONSIBILITY_UNKNOWN",
                    confidence=0.0,
                )
            )
            continue
        ranked = []
        status_rank = {"not_observed": 0, "partial": 1, "matched": 2}
        for candidate in candidates:
            status, rules, confidence = _text_match(
                requirement.text, candidate.text, config
            )
            ranked.append(
                (
                    status_rank[status],
                    confidence,
                    candidate.experience_id,
                    candidate.text,
                    status,
                    rules,
                    candidate.evidence,
                    candidate.resolution_status,
                )
            )
        ranked.sort(
            key=lambda item: (
                item[0],
                item[1],
                item[7] == "resolved" and bool(item[6]),
                item[2],
                item[3],
            ),
            reverse=True,
        )
        best = ranked[0]
        (
            _, confidence, experience_id, candidate_text, status, rules, evidence,
            resolution_status,
        ) = best
        if resolution_status != "resolved" and status in {"matched", "partial"}:
            status = "unresolved"
            rules = ()
            confidence = 0.0
            reason = "CANDIDATE_RESPONSIBILITY_UNRESOLVED"
        elif status in {"matched", "partial"} and not evidence:
            status = "unknown"
            rules = ()
            confidence = 0.0
            reason = "CANDIDATE_EVIDENCE_UNKNOWN"
        elif status == "matched":
            reason = "RESPONSIBILITY_MATCHED"
        elif status == "partial":
            reason = "RESPONSIBILITY_PARTIALLY_MATCHED"
        else:
            reason = "RESPONSIBILITY_NOT_OBSERVED"
        candidate_evidence = evidence
        if status in {"matched", "partial"} and evidence:
            candidate_evidence = _dedupe_evidence(
                tuple(
                    item[6]
                    for item in ranked[: config.max_candidate_evidence]
                    if item[0] == status_rank[status]
                    and item[7] == "resolved"
                    and item[6]
                )
            )
        results.append(
            ResponsibilityResult(
                requirement_id=requirement_id,
                position_requirement=requirement.text,
                candidate_experience_id=experience_id,
                candidate_experience=candidate_text,
                match_status=status,
                matching_rules=rules,
                position_evidence=position_evidence,
                candidate_evidence=candidate_evidence,
                reason_code=reason,
                confidence=confidence,
            )
        )
    return tuple(results)


def _position_responsibilities(
    position: PositionMatchProfile,
) -> tuple[PositionResponsibilityRequirement, ...]:
    if position.responsibility_requirements:
        return position.responsibility_requirements
    return tuple(
        PositionResponsibilityRequirement(
            requirement_id=f"responsibility:{index}",
            text=text,
            evidence_refs=_position_evidence(text, position.evidence_refs),
        )
        for index, text in enumerate(position.core_responsibilities, 1)
    )


def _project_feature_groups(
    cv: CVMatchProfile,
    project: ExperienceFeature,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[Evidence, ...], bool]:
    features = _linked_features(cv, project)
    resolved_features = tuple(
        item for item in features if item.resolution_status == "resolved"
    )
    tasks = tuple(project.responsibilities) + tuple(
        item.raw_text for item in resolved_features
        if "achievement" not in item.source_scope.casefold()
        and "highlight" not in item.source_scope.casefold()
    )
    achievements = tuple(
        item.raw_text
        for item in resolved_features
        if "achievement" in item.source_scope.casefold()
        or "highlight" in item.source_scope.casefold()
    )
    evidence = _dedupe_evidence(
        (project.evidence_refs,) + tuple(item.evidence_refs for item in resolved_features)
    )
    return tasks, achievements, evidence, len(resolved_features) != len(features)


def _project_required_skill_coverage(
    cv: CVMatchProfile,
    project: ExperienceFeature,
    position: PositionMatchProfile,
    *,
    canonical_identity_enabled: bool,
) -> tuple[tuple[str, ...], bool]:
    """Return position-side skill ids demonstrated by one experience.

    Standard-position and CV taxonomies can legitimately publish different ids
    for the same canonical skill.  Keep exact-id matching first, then permit the
    same conservative canonical-name equality already used by the formal skill
    matcher.  No fuzzy alias or substring matching is introduced here.
    """

    project_skill_ids = set(project.tool_skill_ids)
    covered = {
        item.skill_id
        for item in position.required_skills
        if item.resolution_status == "resolved"
        and item.skill_id is not None
        and item.skill_id in project_skill_ids
    }
    if not canonical_identity_enabled:
        return tuple(sorted(covered)), False

    cv_names_by_skill_id: dict[str, set[str]] = {}
    for item in (*cv.skills, *cv.capability_profiles):
        if (
            item.resolution_status != "resolved"
            or item.skill_id is None
            or item.canonical_name is None
        ):
            continue
        cv_names_by_skill_id.setdefault(item.skill_id, set()).add(
            unicodedata.normalize("NFKC", item.canonical_name).strip().casefold()
        )
    project_skill_names = {
        name
        for skill_id in project_skill_ids
        for name in cv_names_by_skill_id.get(skill_id, ())
    }
    canonical_coverage = {
        item.skill_id
        for item in position.required_skills
        if item.resolution_status == "resolved"
        and item.skill_id is not None
        and item.canonical_name is not None
        and unicodedata.normalize("NFKC", item.canonical_name).strip().casefold()
        in project_skill_names
    }
    return tuple(sorted(covered | canonical_coverage)), bool(canonical_coverage - covered)


def evaluate_projects(
    cv: CVMatchProfile,
    position: PositionMatchProfile,
    config: ContextMatchingConfig,
    *,
    canonical_skill_identity_enabled: bool = False,
    include_work_experiences: bool = False,
) -> tuple[ProjectResult, ...]:
    required_skills = tuple(
        sorted(
            item.skill_id
            for item in position.required_skills
            if item.resolution_status == "resolved" and item.skill_id is not None
        )
    )
    position_evidence = _dedupe_evidence(
        tuple(item.evidence_refs for item in position.required_skills)
        + (position.evidence_refs,)
    )
    requirement = required_skills + tuple(position.core_responsibilities)
    if not requirement:
        return ()
    unresolved_required = any(
        item.resolution_status != "resolved" for item in position.required_skills
    )
    if unresolved_required:
        return (
            ProjectResult(
                requirement_id="project:required_skill_combination",
                position_requirement=requirement,
                candidate_experience_id=None,
                candidate_experience=None,
                candidate_role=None,
                required_skill_ids=required_skills,
                match_status="unresolved",
                position_evidence=position_evidence,
                reason_code="PROJECT_REQUIREMENT_UNRESOLVED",
                confidence=0.0,
            ),
        )
    experiences = cv.projects + (cv.work_experiences if include_work_experiences else ())
    if not experiences:
        return (
            ProjectResult(
                requirement_id="project:required_skill_combination",
                position_requirement=requirement,
                candidate_experience_id=None,
                candidate_experience=None,
                candidate_role=None,
                required_skill_ids=required_skills,
                match_status="unknown",
                position_evidence=position_evidence,
                reason_code="CANDIDATE_PROJECT_UNKNOWN",
                confidence=0.0,
            ),
        )
    ranked = []
    for project in experiences:
        tasks, achievements, evidence, has_unresolved_features = _project_feature_groups(
            cv, project
        )
        covered, used_canonical_identity = _project_required_skill_coverage(
            cv,
            project,
            position,
            canonical_identity_enabled=canonical_skill_identity_enabled,
        )
        skill_ratio = len(covered) / len(required_skills) if required_skills else 0.0
        text_matches = tuple(
            _text_match(required, task, config)
            for required in position.core_responsibilities
            for task in tasks
        )
        text_score = max(
            (2 if status == "matched" else 1 if status == "partial" else 0)
            for status, _, _ in text_matches
        ) if text_matches else 0
        role_score = _text_match(position.canonical_title, project.role or "", config)[0]
        role_matched = role_score in {"matched", "partial"}
        scenario_overlap = bool(
            {normalize_text(item) for item in project.business_scenarios}.intersection(
                normalize_text(item) for item in position.business_scenarios.values
            )
        )
        rules = []
        if covered:
            rules.append(
                "standard_skill_canonical_name_overlap"
                if used_canonical_identity
                else "standard_skill_id_overlap"
            )
        if text_score:
            rules.append("project_task_overlap")
        if role_matched:
            rules.append("project_role_overlap")
        if achievements:
            rules.append("project_achievement_evidence")
        if scenario_overlap:
            rules.append("business_scenario_exact")
        evidence_complete = bool(evidence) and bool(position_evidence)
        matched = (
            bool(required_skills)
            and skill_ratio == 1.0
            and (text_score > 0 or role_matched or scenario_overlap)
        )
        partial = bool(covered) or text_score > 0 or role_matched or scenario_overlap
        status = "matched" if matched else "partial" if partial else "not_observed"
        if status == "not_observed" and has_unresolved_features:
            status = "unresolved"
        if status in {"matched", "partial"} and not evidence_complete:
            status = "unknown"
            rules = []
        confidence = (
            min(1.0, 0.55 * skill_ratio + 0.25 * min(text_score, 1) + 0.1 * role_matched
                + 0.05 * bool(achievements) + 0.05 * scenario_overlap)
            if status in {"matched", "partial"}
            else 0.0
        )
        ranked.append(
            (
                {
                    "unknown": -2,
                    "unresolved": -1,
                    "not_observed": 0,
                    "partial": 1,
                    "matched": 2,
                }[status],
                confidence,
                project.experience_id,
                project,
                tasks,
                achievements,
                evidence,
                covered,
                status,
                tuple(rules),
            )
        )
    best = max(ranked, key=lambda item: (item[0], item[1], item[2]))
    _, confidence, _, project, tasks, achievements, evidence, covered, status, rules = best
    reason = {
        "matched": "PROJECT_SKILL_COMBINATION_MATCHED",
        "partial": "PROJECT_PARTIALLY_MATCHED",
        "not_observed": "PROJECT_NOT_OBSERVED",
        "unknown": "PROJECT_EVIDENCE_UNKNOWN",
        "unresolved": "CANDIDATE_PROJECT_UNRESOLVED",
    }[status]
    if status == "not_observed":
        return (
            ProjectResult(
                requirement_id="project:required_skill_combination",
                position_requirement=requirement,
                candidate_experience_id=None,
                candidate_experience=None,
                candidate_role=None,
                required_skill_ids=required_skills,
                match_status=status,
                position_evidence=position_evidence,
                reason_code=reason,
                confidence=confidence,
            ),
        )
    return (
        ProjectResult(
            requirement_id="project:required_skill_combination",
            position_requirement=requirement,
            candidate_experience_id=project.experience_id,
            candidate_experience=" | ".join(tasks) or None,
            candidate_role=project.role,
            candidate_tasks=tasks,
            candidate_achievements=achievements,
            required_skill_ids=required_skills,
            covered_skill_ids=covered,
            match_status=status,
            matching_rules=rules,
            position_evidence=position_evidence,
            candidate_evidence=evidence,
            reason_code=reason,
            confidence=confidence,
        ),
    )


def _industry_candidates(cv: CVMatchProfile) -> tuple[_CandidateText, ...]:
    values = []
    for feature in cv.match_features:
        industry = feature.structured_values.get("industry")
        if isinstance(industry, str):
            values.append(
                _CandidateText(
                    feature.source_object_id,
                    industry,
                    feature.evidence_refs,
                    "industry",
                    feature.resolution_status,
                )
            )
    return tuple(values)


def _scenario_candidates(cv: CVMatchProfile) -> tuple[_CandidateText, ...]:
    return tuple(
        _CandidateText(
            item.experience_id,
            scenario,
            _matching_evidence(scenario, item.evidence_refs),
            "business_scenario",
            "resolved",
        )
        for item in cv.work_experiences + cv.projects
        for scenario in item.business_scenarios
    )


def _evaluate_scenario_type(
    cv: CVMatchProfile,
    position: PositionMatchProfile,
    requirements: tuple[str, ...],
    position_evidence_pool: tuple[Evidence, ...],
    candidates: tuple[_CandidateText, ...],
    scenario_type: Literal["industry", "business_scenario"],
    config: ContextMatchingConfig,
) -> tuple[ScenarioResult, ...]:
    results = []
    for index, requirement in enumerate(requirements):
        evidence = _position_evidence(requirement, position_evidence_pool)
        if _unresolved_requirement(cv, scenario_type, requirement) or (
            _unresolved_requirement(position, scenario_type, requirement)
        ):
            status, reason, confidence = "unresolved", "SCENARIO_UNRESOLVED", 0.0
            best = None
            rules: tuple[str, ...] = ()
        elif not evidence:
            status, reason, confidence = "unknown", "POSITION_EVIDENCE_UNKNOWN", 0.0
            best = None
            rules = ()
        elif not candidates:
            status, reason, confidence = "unknown", "CANDIDATE_SCENARIO_UNKNOWN", 0.0
            best = None
            rules = ()
        else:
            ranked = []
            for candidate in candidates:
                candidate_status, text_rules, candidate_confidence = _text_match(
                    requirement, candidate.text, config
                )
                ranked.append(
                    (
                        {"not_observed": 0, "partial": 1, "matched": 2}[candidate_status],
                        candidate_confidence,
                        candidate.experience_id,
                        candidate,
                        candidate_status,
                        text_rules,
                    )
                )
            _, confidence, _, best, status, rules = max(
                ranked,
                key=lambda item: (
                    item[3].resolution_status == "resolved" and bool(item[3].evidence),
                    item[0],
                    item[1],
                    item[2],
                ),
            )
            if best.resolution_status != "resolved" and status in {"matched", "partial"}:
                status, reason, confidence, rules = (
                    "unresolved", "CANDIDATE_SCENARIO_UNRESOLVED", 0.0, ()
                )
            elif status in {"matched", "partial"} and not best.evidence:
                status, reason, confidence, rules = (
                    "unknown", "CANDIDATE_EVIDENCE_UNKNOWN", 0.0, ()
                )
            elif status == "matched":
                rules = ("normalized_name_exact",)
                confidence = config.normalized_name_confidence
                reason = "SCENARIO_MATCHED"
            elif status == "partial":
                reason = "SCENARIO_PARTIALLY_MATCHED"
            else:
                reason = "SCENARIO_NOT_OBSERVED"
        results.append(
            ScenarioResult(
                requirement_id=f"{scenario_type}:{index + 1}",
                scenario_type=scenario_type,
                position_requirement=requirement,
                candidate_experience_id=best.experience_id if best else None,
                candidate_experience=best.text if best else None,
                match_status=status,
                matching_rules=rules,
                position_evidence=evidence,
                candidate_evidence=best.evidence if best else (),
                reason_code=reason,
                confidence=confidence,
            )
        )
    return tuple(results)


def evaluate_scenarios(
    cv: CVMatchProfile,
    position: PositionMatchProfile,
    config: ContextMatchingConfig,
) -> tuple[ScenarioResult, ...]:
    return _evaluate_scenario_type(
        cv,
        position,
        position.industries.values,
        position.industries.evidence_refs,
        _industry_candidates(cv),
        "industry",
        config,
    ) + _evaluate_scenario_type(
        cv,
        position,
        position.business_scenarios.values,
        position.business_scenarios.evidence_refs,
        _scenario_candidates(cv),
        "business_scenario",
        config,
    )


def context_coverage(results: tuple[object, ...], partial_weight: float) -> float | None:
    evaluable = tuple(
        item
        for item in results
        if item.match_status not in {"unknown", "unresolved"}
        and getattr(item, "status_detail", None)
        not in {"uncertain", "insufficient_evidence"}
    )
    if not evaluable:
        return None
    score = sum(
        1.0 if item.match_status == "matched"
        else partial_weight if item.match_status == "partial"
        else 0.0
        for item in evaluable
    )
    return score / len(evaluable)
