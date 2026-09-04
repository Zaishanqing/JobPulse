from __future__ import annotations

from dataclasses import dataclass


class MatchingRuleViolation(ValueError):
    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


@dataclass(frozen=True)
class ResumeSkillIdentity:
    skill_id: str
    confidence: float = 0.9
    proficiency: str | None = None


@dataclass(frozen=True)
class ResumeProject:
    project_name: str
    description: str | None
    skill_ids: tuple[str, ...]
    evidence: str | None


@dataclass(frozen=True)
class MatchSkill:
    skill_id: str
    skill_name: str
    raw_skill: str | None
    category: str
    weight: float
    confidence: float
    importance_level: str
    trend_score: float
    evidence_count: int
    created_at: str | None = None


@dataclass(frozen=True)
class RadarMetric:
    dimension: str
    score: float | None
    measurement_status: str


@dataclass(frozen=True)
class MatchOutcome:
    overall_score: float
    matched: tuple[MatchSkill, ...]
    missing: tuple[MatchSkill, ...]
    bonus: tuple[MatchSkill, ...]
    radar: tuple[RadarMetric, ...]


@dataclass(frozen=True)
class ProjectEvidence:
    project_name: str
    matched_skills: tuple[str, ...]
    evidence: str | None
    rule: str


@dataclass(frozen=True)
class ProjectMatch:
    matched_project_count: int
    summary: str
    evidence: tuple[ProjectEvidence, ...]


@dataclass(frozen=True)
class MatchExplanation:
    summary: str
    generate_learning_path: bool
    implementation_status: str
    unavailable_dimensions: tuple[str, ...]


@dataclass(frozen=True)
class LearningStage:
    stage: int
    start_week: int
    end_week: int
    goal: str
    skills: tuple[str, ...]


# Local matching execution lives exclusively in matching-service.
