from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from app.domain.input_limits import MAX_JD_TEXT_CHARS
from app.domain.json_types import JsonObject, freeze_json_object


PARSE_REVIEW_THRESHOLD = 0.85
PARSE_PRIORITY_REVIEW_THRESHOLD = 0.6
DUPLICATE_SIMILARITY_THRESHOLD = 0.5
DUPLICATE_REVIEW_THRESHOLD = 0.7
INFLATION_REVIEW_THRESHOLD = 0.7

LEGACY_PARSE_EDIT_FIELDS = frozenset({
    "position_title",
    "responsibilities",
    "required_skills",
    "bonus_skills",
    "education",
    "experience",
    "industry",
    "tools",
    "business_scenarios",
})
EDITABLE_PARSE_FIELDS = frozenset({
    "parse_confidence",
    "need_review",
    "extraction_result",
    "normalized_result",
})


class JDPolicyViolation(ValueError):
    pass


def validate_jd_raw_text(raw_text: str) -> None:
    if not isinstance(raw_text, str):
        raise JDPolicyViolation("JD raw_text must be a string")
    if len(raw_text) > MAX_JD_TEXT_CHARS:
        raise JDPolicyViolation(
            f"JD raw_text must not exceed {MAX_JD_TEXT_CHARS} characters"
        )


@dataclass(frozen=True)
class ParseFacts:
    title: str = ""
    raw_text: str = ""
    required_field_coverage: float = 0.0
    exact_evidence_ratio: float = 0.0
    unresolved_ratio: float = 1.0
    normalization_coverage: float = 0.0
    schema_provider_valid: bool = False
    provider_requires_review: bool = False
    business_scenarios: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParseDecision:
    parse_quality_score: float
    need_review: bool
    business_scenarios: tuple[str, ...]
    quality_level: Literal["high", "medium", "low"]
    review_priority: Literal["normal", "high"] | None
    quality_components: tuple[tuple[str, float], ...]

    @property
    def parse_confidence(self) -> float:
        """Compatibility alias for the persisted/API field."""
        return self.parse_quality_score


@dataclass(frozen=True)
class JDParseCommand:
    extraction_mode: Literal["llm", "rule"]
    model: str = "default"
    use_skill_dictionary: bool = True
    auto_normalize_skill: bool = True


@dataclass(frozen=True)
class JDParseEditCommand:
    changed_fields: frozenset[str]
    parse_confidence: float | None = None
    need_review: bool | None = None
    extraction_result: JsonObject | None = None
    normalized_result: JsonObject | None = None

    def __post_init__(self) -> None:
        supported = EDITABLE_PARSE_FIELDS | LEGACY_PARSE_EDIT_FIELDS
        invalid = sorted(self.changed_fields - supported)
        if invalid:
            raise JDPolicyViolation(
                f"Unsupported JD parse edit fields: {', '.join(invalid)}"
            )
        if "parse_confidence" in self.changed_fields:
            if (
                self.parse_confidence is None
                or isinstance(self.parse_confidence, bool)
                or not isinstance(self.parse_confidence, (int, float))
                or not 0 <= self.parse_confidence <= 1
            ):
                raise JDPolicyViolation("parse_confidence must be between 0 and 1")
        if self.extraction_result is not None:
            object.__setattr__(self, "extraction_result", freeze_json_object(
                self.extraction_result, field="extraction_result"
            ))
        if self.normalized_result is not None:
            object.__setattr__(self, "normalized_result", freeze_json_object(
                self.normalized_result, field="normalized_result"
            ))

    @property
    def changes_versioned_result(self) -> bool:
        return bool(
            self.changed_fields & {"extraction_result", "normalized_result"}
        )


@dataclass(frozen=True)
class ParseReviewFacts:
    current_need_review: bool
    current_workflow_status: str


@dataclass(frozen=True)
class ParseEditDecision:
    need_review: bool | None
    workflow_status: str | None


@dataclass(frozen=True)
class DuplicateCandidateFacts:
    jd_id: str
    raw_text: str
    source_name: str | None
    required_skill_ids: tuple[str, ...] = ()
    bonus_skill_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class DuplicateFacts:
    jd_id: str
    raw_text: str
    candidates: tuple[DuplicateCandidateFacts, ...]
    required_skill_ids: tuple[str, ...] = ()
    bonus_skill_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SimilarJDDecision:
    jd_id: str
    similarity: float
    source_name: str | None
    text_overlap: float = 0.0
    skill_overlap: float = 0.0
    length_similarity: float = 0.0


@dataclass(frozen=True)
class DuplicateDecision:
    copy_risk_score: float
    similar_jds: tuple[SimilarJDDecision, ...]
    recommended_action: str
    reason: str


@dataclass(frozen=True)
class InflationFacts:
    title: str
    required_skill_names: tuple[str, ...]
    career_level: str | None = None
    min_experience_years: float | None = None
    responsibilities: tuple[str, ...] = ()
    ownership_signals: tuple[str, ...] = ()
    leadership_signals: tuple[str, ...] = ()


@dataclass(frozen=True)
class AbnormalSkillDecision:
    skill_id: str
    skill_name: str
    reason: str


@dataclass(frozen=True)
class InflationDecision:
    inflation_score: float
    abnormal_skills: tuple[AbnormalSkillDecision, ...]
    recommended_action: str
    mismatch_reasons: tuple[str, ...] = ()


def requires_parse_review(confidence: float) -> bool:
    return confidence < PARSE_REVIEW_THRESHOLD


def evaluate_parse(facts: ParseFacts) -> ParseDecision:
    components = (
        ("required_field_coverage", facts.required_field_coverage),
        ("exact_evidence_ratio", facts.exact_evidence_ratio),
        ("unresolved_quality", 1.0 - facts.unresolved_ratio),
        ("normalization_coverage", facts.normalization_coverage),
        ("schema_provider_validation", 1.0 if facts.schema_provider_valid else 0.0),
    )
    raw_score = (
        (0.30 * facts.required_field_coverage)
        + (0.30 * facts.exact_evidence_ratio)
        + (0.15 * (1.0 - facts.unresolved_ratio))
        + (0.20 * facts.normalization_coverage)
        + (0.05 * (1.0 if facts.schema_provider_valid else 0.0))
    )
    if raw_score >= PARSE_REVIEW_THRESHOLD:
        quality_level: Literal["high", "medium", "low"] = "high"
        review_priority: Literal["normal", "high"] | None = (
            "normal" if facts.provider_requires_review else None
        )
    elif raw_score >= PARSE_PRIORITY_REVIEW_THRESHOLD:
        quality_level = "medium"
        review_priority = "normal"
    else:
        quality_level = "low"
        review_priority = "high"
    return ParseDecision(
        parse_quality_score=round(raw_score, 2),
        need_review=review_priority is not None,
        business_scenarios=facts.business_scenarios,
        quality_level=quality_level,
        review_priority=review_priority,
        quality_components=tuple((name, round(value, 4)) for name, value in components),
    )


def evaluate_parse_edit(
    facts: ParseReviewFacts, command: JDParseEditCommand
) -> ParseEditDecision:
    validate_parse_edit_command(command)
    if command.changes_versioned_result:
        return ParseEditDecision(need_review=True, workflow_status="draft")
    if "need_review" in command.changed_fields:
        return ParseEditDecision(command.need_review, None)
    if "parse_confidence" in command.changed_fields:
        return ParseEditDecision(
            requires_parse_review(command.parse_confidence or 0.0), None
        )
    return ParseEditDecision(None, None)


def validate_parse_edit_command(command: JDParseEditCommand) -> None:
    legacy = sorted(command.changed_fields & LEGACY_PARSE_EDIT_FIELDS)
    if legacy:
        raise JDPolicyViolation(
            "Legacy compatibility fields are read-only; edit extraction_result "
            f"or normalized_result instead: {', '.join(legacy)}"
        )


def duplicate_action(score: float) -> str:
    return "downweight" if score >= DUPLICATE_REVIEW_THRESHOLD else "keep"


def is_similar_duplicate(similarity: float) -> bool:
    return similarity >= DUPLICATE_SIMILARITY_THRESHOLD


def _skill_id_set(
    required_skill_ids: tuple[str, ...],
    bonus_skill_ids: tuple[str, ...],
) -> frozenset[str]:
    return frozenset(
        skill_id
        for skill_id in (*required_skill_ids, *bonus_skill_ids)
        if skill_id
    )


def _normalize_duplicate_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower()
    punctuation_normalized = "".join(
        " " if unicodedata.category(character).startswith("P") else character
        for character in normalized
    )
    return re.sub(r"\s+", " ", punctuation_normalized).strip()


def _character_shingles(text: str) -> frozenset[str]:
    compact = text.replace(" ", "")
    if not compact:
        return frozenset()
    shingles = {
        compact[index : index + size]
        for size in (3, 4)
        for index in range(max(0, len(compact) - size + 1))
    }
    return frozenset(shingles or {compact})


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _length_similarity(left: str, right: str) -> float:
    left = left.replace(" ", "")
    right = right.replace(" ", "")
    longest = max(len(left), len(right))
    return 1 - (abs(len(left) - len(right)) / longest) if longest else 0.0


def evaluate_duplicate(facts: DuplicateFacts) -> DuplicateDecision:
    source_text = _normalize_duplicate_text(facts.raw_text)
    source_shingles = _character_shingles(source_text)
    source_skill_ids = _skill_id_set(
        facts.required_skill_ids, facts.bonus_skill_ids
    )
    similar: list[SimilarJDDecision] = []
    candidate_scores: list[float] = []
    for candidate in facts.candidates:
        candidate_text = _normalize_duplicate_text(candidate.raw_text)
        text_overlap = _jaccard(
            source_shingles, _character_shingles(candidate_text)
        )
        skill_overlap = _jaccard(
            source_skill_ids,
            _skill_id_set(candidate.required_skill_ids, candidate.bonus_skill_ids),
        )
        length_similarity = _length_similarity(source_text, candidate_text)
        similarity = round(
            (0.65 * text_overlap)
            + (0.20 * skill_overlap)
            + (0.15 * length_similarity),
            2,
        )
        candidate_scores.append(similarity)
        if is_similar_duplicate(similarity):
            similar.append(
                SimilarJDDecision(
                    candidate.jd_id,
                    similarity,
                    candidate.source_name,
                    round(text_overlap, 2),
                    round(skill_overlap, 2),
                    round(length_similarity, 2),
                )
            )
    score = max(candidate_scores, default=0.0)
    return DuplicateDecision(
        score,
        tuple(similar),
        duplicate_action(score),
        "基于字符 shingles 文本重合度、技能重合度和长度相似度的确定性重复评分",
    )


def inflation_action(score: float) -> str:
    return "manual_review" if score >= INFLATION_REVIEW_THRESHOLD else "keep"


_JUNIOR_TITLE_MARKERS = ("实习", "初级", "助理", "junior", "entry level")
_ARCHITECTURE_SIGNALS = (
    "整体架构",
    "架构设计",
    "架构规划",
    "技术选型",
    "系统规划",
    "enterprise architecture",
    "technical strategy",
)
_OWNERSHIP_SIGNALS = (
    "主导",
    "总体负责",
    "全面负责",
    "端到端负责",
    "负责大型系统",
    "业务 owner",
    "technical owner",
    "end to end ownership",
)
_LEADERSHIP_SIGNALS = (
    "带团队",
    "带领团队",
    "管理团队",
    "人员管理",
    "团队建设",
    "部门管理",
    "绩效管理",
    "people management",
    "team leadership",
)


def _contains_signal(text: str, signals: tuple[str, ...]) -> bool:
    normalized = _normalize_duplicate_text(text)
    return any(signal in normalized for signal in signals)


def evaluate_inflation(facts: InflationFacts) -> InflationDecision:
    career_level = (facts.career_level or "").lower()
    title = _normalize_duplicate_text(facts.title)
    low_seniority = career_level in {"intern", "junior"} or (
        career_level in {"", "unspecified"}
        and any(marker in title for marker in _JUNIOR_TITLE_MARKERS)
    )
    low_experience = (
        facts.min_experience_years is not None
        and facts.min_experience_years <= 2
    )
    responsibility_text = " ".join(facts.responsibilities)
    architecture_signal = _contains_signal(
        responsibility_text, _ARCHITECTURE_SIGNALS
    )
    ownership_signal = bool(facts.ownership_signals) or _contains_signal(
        responsibility_text, _OWNERSHIP_SIGNALS
    )
    leadership_signal = bool(facts.leadership_signals) or _contains_signal(
        responsibility_text, _LEADERSHIP_SIGNALS
    )
    senior_responsibility = (
        architecture_signal or ownership_signal or leadership_signal
    )

    score = 0.0
    reasons: list[str] = []
    if low_seniority and architecture_signal:
        score += 0.25
        reasons.append(
            "seniority_mismatch: 低职级与整体架构或技术选型职责不一致"
        )
    if low_experience and senior_responsibility:
        score += 0.25
        reasons.append(
            "experience_mismatch: 低经验要求与高阶责任范围不一致"
        )
    if (low_seniority or low_experience) and ownership_signal:
        score += 0.25
        reasons.append(
            "ownership_mismatch: 低资历要求与主导或端到端负责不一致"
        )
    if (low_seniority or low_experience) and leadership_signal:
        score += 0.15
        reasons.append(
            "leadership_mismatch: 低资历要求与团队管理责任不一致"
        )
    if len(set(facts.required_skill_names)) >= 9:
        score += 0.10
        reasons.append("skill_breadth: 必备技能范围较广，仅作为弱信号")

    score = round(min(score, 1.0), 2)
    return InflationDecision(
        score,
        (),
        inflation_action(score),
        tuple(reasons),
    )
