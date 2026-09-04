from dataclasses import dataclass

from app.domain.json_types import FrozenJsonObject


class TrendRuleViolation(ValueError):
    pass


@dataclass(frozen=True)
class TrendSkill:
    skill_id: str
    skill_name: str
    category: str
    weight: float
    confidence: float
    importance_level: str
    trend_score: float
    evidence_count: int
    created_at: str | None = None
    growth_rate: float | None = None
    trend_direction: str | None = None
    evidence_references: tuple[str, ...] = ()
    quality_flags: tuple[str, ...] = ()
    score_explanation: FrozenJsonObject | None = None
    current_window_signal: float | None = None
    historical_window_signal: float | None = None


@dataclass(frozen=True)
class TrendRelation:
    source: str
    target: str
    relation_type: str
    weight: float


@dataclass(frozen=True)
class TrendGraphSnapshot:
    position_id: str
    position_name: str
    graph_version: str
    skills: tuple[TrendSkill, ...]
    relations: tuple[TrendRelation, ...]
    core_responsibilities: tuple[str, ...]
    industry_scenarios: tuple[str, ...]
    status: str


@dataclass(frozen=True)
class SkillWeightDistribution:
    core: tuple[TrendSkill, ...]
    high: tuple[TrendSkill, ...]
    bonus: tuple[TrendSkill, ...]
    edge: tuple[TrendSkill, ...]


@dataclass(frozen=True)
class SkillReplacement:
    declining_skill: TrendSkill
    replacement_skill_name: str
    reason: str


@dataclass(frozen=True)
class SkillComboShift:
    from_combo: tuple[str, ...]
    to_combo: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class TrendRisk:
    risk_type: str
    level: str
    reason: str
